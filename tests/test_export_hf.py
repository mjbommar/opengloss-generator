"""``export-hf``: the v2.0 Hugging Face release family (D-72).

Entirely offline. Entries are built with the schema directly, exported to a temporary
directory, and then read back *out of the written parquet files* — the point of these
tests is that what a consumer downloads is what the card says it is, so nothing is
asserted against an in-memory intermediate that a writing bug could bypass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.export import hf, hf_cards, hf_rows, hf_schemas
from opengloss_generator.export.hf_rows import TierIndex
from opengloss_generator.export.hf_schemas import REPOS, REPOS_BY_SLUG, resolve_repos
from opengloss_generator.identity import edge_id
from opengloss_generator.schema import Contrast as SchemaContrast
from opengloss_generator.schema import (
    ContrastVerdict,
    Difficulty,
    Etymology,
    EtymologySegment,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Provenance,
    QAPair,
    Query,
    QueryStyle,
    QuestionType,
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

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _gloss(text: str) -> Renditions[str]:
    """Return a gloss set holding only the canonical rendition."""
    return Renditions[str](root=[canonical_rendition(text)])


def _example(
    text: str,
    *,
    level: ReadingLevel = ReadingLevel.NEUTRAL,
    span: tuple[int, int] | None = None,
    provenance_id: str | None = None,
) -> Rendition[Example]:
    """Return one example rendition."""
    return Rendition[Example](
        reading_level=level,
        style=Register.PLAIN,
        content=Example(text=text, span=span),
        provenance_id=provenance_id,
    )


def _sense(index: int, gloss: str, **kwargs: object) -> Sense:
    """Return a sense with a canonical gloss and whatever else the test needs."""
    return Sense(index=index, gloss=_gloss(gloss), domain=DomainTag.NATURE_GENERAL, **kwargs)


def _entry(headword: str, senses: list[Sense], **kwargs: object) -> Lexeme:
    """Return a one-POS noun entry."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
        **kwargs,
    )


def _rich_entry() -> Lexeme:
    """Return one entry carrying every field the export projects."""
    live = _sense(
        0,
        "A raised bank of earth.",
        examples=Renditions[Example](
            root=[
                _example("The ridge rose above the valley.", span=(4, 9)),
                _example("A ridge of soil.", level=ReadingLevel.GRADE_1),
            ]
        ),
        relations=[
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="crest", sense_id="crest:noun:0", confidence=0.9),
            ),
            Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="landform")),
        ],
        queries=[
            Query(text="long raised strip of land", style=QueryStyle.KEYWORD),
            Query(text="what is a ridge in geography", style=QueryStyle.QUESTION),
        ],
        qa=[
            QAPair(
                question="What is a ridge?",
                answer="A raised bank of earth.",
                question_type=QuestionType.DEFINITION,
                difficulty=Difficulty.EASY,
                grounded_in=["ridge:noun:0#neutral/plain"],
            )
        ],
    )
    retired = Sense(index=1, gloss=_gloss("An obsolete meaning."), retired=True)
    entry = _entry(
        "ridge",
        [live, retired],
        etymology=Etymology(
            summary="From Old English hrycg.",
            segments=[EtymologySegment(language="Old English", form="hrycg", meaning="back")],
            cognates=["German Rücken"],
            references=["https://example.invalid/ridge"],
        ),
        encyclopedia=Renditions[str](
            root=[
                canonical_rendition("A ridge is an elongated elevation of terrain."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_5,
                    style=Register.PLAIN,
                    content="A ridge is a long, high strip of land.",
                ),
            ]
        ),
        lexical_explanation=Renditions[str](root=[canonical_rendition("Why 'ridge' exists.")]),
        contrasts=[
            SchemaContrast(
                edge_id=edge_id("ridge:noun:0", "synonym", "crest"),
                target_sense_id="crest:noun:0",
                text=Renditions[str](
                    root=[canonical_rendition("A ridge is longer; a crest is a peak.")]
                ),
                verdict=ContrastVerdict.RELATED_AS_TYPED,
            )
        ],
    )
    entry.add_provenance(
        Provenance(
            stage=StageName.SENSES,
            model="gpt-5.6-luna",
            prompt_version="7",
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.001,
        )
    )
    return entry


def _target_entry() -> Lexeme:
    """Return the far end of ``ridge``'s contrast, so the target gloss resolves."""
    return _entry("crest", [_sense(0, "The top of a hill or wave.")])


def _export(tmp_path: Path, entries: list[Lexeme], **kwargs: object) -> hf.HfExportResult:
    """Write ``entries`` to a fresh store and export the whole family from it."""
    store = _store(tmp_path)
    for entry in entries:
        store.write(entry)
    return hf.export_hf(store, tmp_path / "out", **kwargs)


def _read(result: hf.HfExportResult, slug: str, config: str = "default") -> list[dict]:
    """Read one config's rows back out of the parquet files the export wrote."""
    spec = REPOS_BY_SLUG[slug]
    directory = hf.data_dir(result.out_dir, spec, spec.config(config))
    rows: list[dict] = []
    for path in sorted(directory.glob("*.parquet")):
        rows.extend(pq.read_table(path).to_pylist())
    return rows


# --------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------


def test_every_repo_has_a_unique_name_and_at_least_one_config():
    names = [spec.name() for spec in REPOS]
    assert len(names) == len(set(names))
    for spec in REPOS:
        assert spec.configs
        assert spec.blurb
        assert spec.summary
        assert spec.snippet


def test_every_field_is_documented_and_uniquely_named():
    for spec in REPOS:
        for config in spec.configs:
            names = [field.name for field in config.fields]
            assert len(names) == len(set(names)), f"{spec.name()}/{config.name}"
            for field in config.fields:
                assert field.description.strip(), f"{spec.name()}.{field.name}"


def test_resolve_repos_is_registry_ordered_and_rejects_unknown_names():
    assert [spec.slug for spec in resolve_repos("senses,lexicon")] == ["lexicon", "senses"]
    assert resolve_repos("opengloss-v2.1-queries")[0].slug == "queries"
    assert resolve_repos("opengloss-v2.0-queries", release="v2.0")[0].slug == "queries"
    assert len(resolve_repos("all")) == len(REPOS)
    with pytest.raises(ValueError, match="unknown repo"):
        resolve_repos("nope")


def test_data_globs_are_distinct_per_config():
    for spec in REPOS:
        globs = {spec.data_glob(config) for config in spec.configs}
        assert len(globs) == len(spec.configs), spec.name()


# --------------------------------------------------------------------------------------
# Parquet schemas
# --------------------------------------------------------------------------------------


def test_written_parquet_schema_matches_the_declared_schema(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    for spec in REPOS:
        for config in spec.configs:
            directory = hf.data_dir(result.out_dir, spec, config)
            paths = sorted(directory.glob("*.parquet"))
            assert paths, f"{spec.name()}/{config.name} wrote no shard"
            for path in paths:
                assert pq.read_schema(path).equals(config.schema), f"{spec.name()}/{config.name}"


def test_a_row_with_an_unknown_column_is_refused(tmp_path):
    spec = REPOS_BY_SLUG["queries"]
    writer = hf._ShardWriter(
        tmp_path / "w", spec.configs[0].schema, max_rows=10, max_bytes=1_000_000
    )
    with pytest.raises(ValueError, match="not in the schema"):
        writer.write({"query_id": "a:noun:0#q0", "surprise": 1})
    writer.close()


def test_a_config_with_no_rows_still_gets_one_typed_shard(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    rows = _read(result, "relations", "tombstoned")
    assert rows == []
    spec = REPOS_BY_SLUG["relations"]
    path = next(hf.data_dir(result.out_dir, spec, spec.config("tombstoned")).glob("*.parquet"))
    assert pq.read_schema(path).equals(spec.config("tombstoned").schema)


def test_shards_roll_when_the_row_cap_is_reached(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()], shard_rows=1)
    spec = REPOS_BY_SLUG["definitions"]
    paths = sorted(hf.data_dir(result.out_dir, spec, spec.configs[0]).glob("*.parquet"))
    assert len(paths) == result.stats.rows[("definitions", "default")]
    assert [p.name for p in paths[:2]] == ["train-00000.parquet", "train-00001.parquet"]


def test_shards_roll_when_the_byte_cap_is_reached(tmp_path):
    spec = REPOS_BY_SLUG["queries"]
    writer = hf._ShardWriter(
        tmp_path / "w", spec.configs[0].schema, max_rows=1_000_000, max_bytes=1
    )
    for index in range(hf._BATCH_ROWS * 2 + 5):
        writer.write({"query_id": f"a:noun:0#q{index}", "text": "x" * 100})
    shards, size = writer.close()
    assert shards == 3
    assert size > 0
    assert len(sorted((tmp_path / "w").glob("*.parquet"))) == 3


# --------------------------------------------------------------------------------------
# Live-only senses, and the nested/flat agreement
# --------------------------------------------------------------------------------------


def test_retired_senses_are_counted_but_never_exported(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    assert result.stats.retired_senses == 1
    assert result.stats.live_senses == 1
    senses = _read(result, "senses")
    assert [row["sense_id"] for row in senses] == ["ridge:noun:0"]
    assert all(row["sense_id"] == "ridge:noun:0" for row in _read(result, "definitions"))
    lexicon = _read(result, "lexicon")[0]
    assert lexicon["sense_ids"] == ["ridge:noun:0"]
    assert lexicon["n_live_senses"] == 1
    assert lexicon["n_retired_senses"] == 1


def test_the_nested_sense_row_and_the_flat_views_agree(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    nested = next(row for row in _read(result, "senses") if row["sense_id"] == "ridge:noun:0")
    flat_defs = [row for row in _read(result, "definitions") if row["sense_id"] == "ridge:noun:0"]
    assert len(nested["gloss_renditions"]) == len(flat_defs)
    assert nested["gloss"] == flat_defs[0]["text"]
    assert flat_defs[0]["is_canonical"] is True

    flat_examples = [row for row in _read(result, "examples") if row["sense_id"] == "ridge:noun:0"]
    assert nested["n_examples"] == len(flat_examples) == 2
    assert flat_examples[0]["span_start"] == 4
    assert flat_examples[0]["span_end"] == 9
    assert flat_examples[1]["span_start"] is None

    flat_relations = [
        row
        for row in _read(result, "relations", "relations")
        if row["source_sense_id"] == "ridge:noun:0"
    ]
    assert nested["n_relations"] == len(flat_relations) == 2
    assert flat_relations[0]["edge_id"] == "ridge:noun:0-synonym->crest"
    assert flat_relations[0]["resolved"] is True
    assert flat_relations[1]["resolved"] is False
    assert flat_relations[1]["target_sense_id"] is None


def test_queries_record_whether_they_name_their_own_headword(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    rows = {row["query_id"]: row for row in _read(result, "queries")}
    assert rows["ridge:noun:0#q0"]["headword_free"] is True
    assert rows["ridge:noun:0#q1"]["headword_free"] is False
    assert result.stats.queries_headword_free == 1


def test_qa_pairs_keep_their_derived_id_and_citations(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    row = _read(result, "qa-pairs")[0]
    assert row["qa_id"] == "ridge:noun:0#qa0"
    assert row["grounded_in"] == ["ridge:noun:0#neutral/plain"]
    assert row["question_type"] == "definition"


def test_an_example_written_by_the_examples_stage_is_labelled_per_sense(tmp_path):
    entry = _entry("ridge", [_sense(0, "A raised bank.")])
    provenance_id = entry.add_provenance(
        Provenance(stage=StageName.EXAMPLES, model="gpt-5.6-luna", prompt_version="7")
    )
    entry.pos_entries[0].senses[0].examples = Renditions[Example](
        root=[
            _example("The ridge is long.", provenance_id=provenance_id),
            _example("A short ridge.", level=ReadingLevel.GRADE_1),
        ]
    )
    result = _export(tmp_path, [entry])
    sources = [row["source"] for row in _read(result, "examples")]
    assert sources == ["per_sense", "renditions"]


def test_the_contrast_row_carries_both_ends(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    row = _read(result, "contrasts")[0]
    assert row["edge_id"] == "ridge:noun:0-synonym->crest"
    assert row["relation_type"] == "synonym"
    assert row["source_sense_id"] == "ridge:noun:0"
    assert row["source_gloss"] == "A raised bank of earth."
    assert row["target_headword"] == "crest"
    assert row["target_gloss"] == "The top of a hill or wave."
    assert row["verdict"] == "related_as_typed"


def test_the_encyclopedia_and_explanation_configs_are_separate(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    encyclopedia = _read(result, "encyclopedia", "encyclopedia")
    explanation = _read(result, "encyclopedia", "explanation")
    assert {row["reading_level"] for row in encyclopedia} == {"neutral", "grade_5"}
    assert len(explanation) == 1
    assert explanation[0]["is_canonical"] is True
    assert encyclopedia[0]["n_words"] > 0


def test_etymology_is_one_row_per_entry_that_has_one(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    rows = _read(result, "etymology")
    assert [row["lexeme_id"] for row in rows] == ["ridge"]
    assert rows[0]["n_segments"] == 1
    assert rows[0]["segments"][0]["language"] == "Old English"
    assert rows[0]["cognates"] == ["German Rücken"]


# --------------------------------------------------------------------------------------
# Inflections (D-75)
# --------------------------------------------------------------------------------------


def _multi_pos_entry() -> Lexeme:
    """Return one headword with a noun, a verb and an adjective POS entry.

    Each POS entry's morphology exercises a different subset of inflected fields plus a
    derivation, so the projection is checked across relation kinds and across POS entries
    of the same lexeme.
    """
    return Lexeme.empty(
        "Record",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[_sense(0, "A stored account of something.")],
                morphology=Morphology(plural="Records", derivations=["Recorder"]),
            ),
            POSEntry(
                pos=PartOfSpeech.VERB,
                senses=[_sense(0, "To set something down for later reference.")],
                morphology=Morphology(
                    past_tense="recorded",
                    past_participle="recorded",
                    present_participle="recording",
                    third_person_singular="records",
                ),
            ),
            POSEntry(
                pos=PartOfSpeech.ADJECTIVE,
                senses=[_sense(0, "Preserved in a fixed form.")],
                morphology=Morphology(comparative="more recorded", superlative="most recorded"),
            ),
        ],
    )


def test_inflection_rows_cover_every_pos_and_the_lemma_row(tmp_path):
    result = _export(tmp_path, [_multi_pos_entry()])
    rows = _read(result, "inflections")

    by_pos = {}
    for row in rows:
        by_pos.setdefault(row["pos"], []).append(row)

    noun = {row["relation"]: row for row in by_pos["noun"]}
    assert set(noun) == {"lemma", "plural", "derivation"}
    assert noun["lemma"]["form"] == "Record"
    assert noun["plural"]["form"] == "Records"
    assert noun["derivation"]["form"] == "Recorder"

    verb = {row["relation"]: row for row in by_pos["verb"]}
    assert set(verb) == {
        "lemma",
        "past_tense",
        "past_participle",
        "present_participle",
        "third_person_singular",
    }
    assert verb["past_tense"]["form"] == "recorded"
    assert verb["present_participle"]["form"] == "recording"

    adjective = {row["relation"]: row for row in by_pos["adjective"]}
    assert set(adjective) == {"lemma", "comparative", "superlative"}
    assert adjective["comparative"]["form"] == "more recorded"

    # Every row carries the shared lexeme_id/headword/tier regardless of POS.
    assert {row["lexeme_id"] for row in rows} == {"record"}
    assert {row["headword"] for row in rows} == {"Record"}
    assert {row["tier"] for row in rows} == {"unknown"}

    assert result.stats.inflection_forms == len(rows) == 3 + 5 + 3
    assert result.stats.inflection_relations["lemma"] == 3


def test_inflection_form_normalized_is_lower_cased(tmp_path):
    result = _export(tmp_path, [_multi_pos_entry()])
    rows = _read(result, "inflections")
    for row in rows:
        assert row["form_normalized"] == row["form"].lower()
    lemma_noun = next(row for row in rows if row["pos"] == "noun" and row["relation"] == "lemma")
    assert lemma_noun["form"] == "Record"
    assert lemma_noun["form_normalized"] == "record"
    comparative = next(row for row in rows if row["relation"] == "comparative")
    assert comparative["form"] == "more recorded"
    assert comparative["form_normalized"] == "more recorded"


def test_provenance_rows_carry_the_cost_and_a_truncated_note(tmp_path):
    entry = _rich_entry()
    entry.add_provenance(
        Provenance(
            stage=StageName.QUERIES,
            model="gpt-5.6-luna",
            prompt_version="7",
            note="x" * 900,
            cost_usd=0.002,
        )
    )
    result = _export(tmp_path, [entry])
    rows = _read(result, "provenance")
    assert [row["provenance_id"] for row in rows] == ["p1", "p2"]
    assert len(rows[1]["note"]) == hf_rows.NOTE_MAX_CHARS + 1
    assert result.stats.provenance_cost_usd == pytest.approx(0.003)
    lexicon = _read(result, "lexicon")[0]
    assert lexicon["provenance_summary"]["n_records"] == 2
    assert lexicon["provenance_summary"]["total_cost_usd"] == pytest.approx(0.003)


def test_rows_come_out_in_lexeme_then_sense_order(tmp_path):
    entries = [
        _entry(word, [_sense(0, f"{word} sense 0."), _sense(1, f"{word} sense 1.")])
        for word in ("zebra", "apple", "mango")
    ]
    result = _export(tmp_path, entries)
    assert [row["sense_id"] for row in _read(result, "senses")] == [
        "apple:noun:0",
        "apple:noun:1",
        "mango:noun:0",
        "mango:noun:1",
        "zebra:noun:0",
        "zebra:noun:1",
    ]


def test_from_list_restricts_every_repo(tmp_path):
    entries = [_entry(word, [_sense(0, f"{word} sense.")]) for word in ("alpha", "beta")]
    result = _export(tmp_path, entries, lexeme_ids=["alpha"])
    assert [row["lexeme_id"] for row in _read(result, "lexicon")] == ["alpha"]
    assert {row["lexeme_id"] for row in _read(result, "pretrain")} == {"alpha"}
    assert {row["lexeme_id"] for row in _read(result, "retrieval-triples")} <= {"alpha"}


def test_repos_selects_which_directories_are_written(tmp_path):
    result = _export(tmp_path, [_rich_entry()], repos="senses,queries")
    written = {path.name for path in result.out_dir.iterdir()}
    assert written == {
        REPOS_BY_SLUG["senses"].name(),
        REPOS_BY_SLUG["queries"].name(),
    }
    # The store pass still ran, so the cards can quote release-wide numbers.
    assert result.stats.lexemes == 1


def test_release_overrides_the_default_repo_naming_everywhere(tmp_path):
    result = _export(tmp_path, [_rich_entry()], repos="senses", release="v2.0")
    written = {path.name for path in result.out_dir.iterdir()}
    assert written == {"opengloss-v2.0-senses"}
    assert result.repos == ["opengloss-v2.0-senses"]
    text = (result.out_dir / "opengloss-v2.0-senses" / "README.md").read_text(encoding="utf-8")
    assert "# OpenGloss v2.0 — Senses" in text
    assert "opengloss-v2.1-" not in text
    assert "opengloss-v2.0-lexicon" in text


# --------------------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------------------


def _write_tier_files(directory: Path, *, with_tier4: bool = False) -> Path:
    """Write the rank TSVs, with one word deliberately on two of the first three.

    Args:
        directory: Where to write the files.
        with_tier4: Also write ``tier4.tsv``, with a ``group`` column carrying both
            ``stopword`` and ``wf10`` values and one multi-word entry, to exercise D-75's
            group-collapsing and space-tolerant slugification.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "core_10k.tsv").write_text(
        "rank\tword\twiki_frequency\n1\tridge\t100\n", encoding="utf-8"
    )
    (directory / "tier2_50k.tsv").write_text(
        "rank\tword\tscore\n10001\tcrest\t0.3\n10002\tridge\t0.2\n", encoding="utf-8"
    )
    (directory / "tier3_final.tsv").write_text(
        "rank\tword\twiki_frequency\n40000\tmarl\t5\n", encoding="utf-8"
    )
    if with_tier4:
        (directory / "tier4.tsv").write_text(
            "rank\tword\twiki_frequency\tgroup\n"
            "1\tthe\t100000\tstopword\n"
            "2\tto be\t5000\tstopword\n"
            "3\tlive people\t400\twf10\n",
            encoding="utf-8",
        )
    return directory


def test_tier_index_prefers_the_higher_list_and_falls_back_to_unknown(tmp_path):
    index = TierIndex.from_dir(_write_tier_files(tmp_path / "core"))
    assert index.tier_of("ridge") == "core"
    assert index.tier_of("crest") == "tier2"
    assert index.tier_of("marl") == "tier3"
    assert index.tier_of("nowhere") == "unknown"


def test_a_missing_tier_directory_is_not_an_error(tmp_path):
    index = TierIndex.from_dir(tmp_path / "absent")
    assert len(index) == 0
    assert index.tier_of("ridge") == "unknown"


def test_a_missing_tsv_is_tolerated_and_logged_not_crashed(tmp_path, capsys, caplog):
    directory = _write_tier_files(tmp_path / "core")
    (directory / "tier3_final.tsv").unlink()
    index = TierIndex.from_dir(directory)
    # The entry that was only on the deleted file falls to unknown, not to an exception.
    assert index.tier_of("marl") == "unknown"
    # The other files are read normally.
    assert index.tier_of("ridge") == "core"
    assert index.tier_of("crest") == "tier2"
    # structlog routes through stdlib logging when something in the process has already
    # configured it (caplog then sees it) and through its own default printer otherwise
    # (capsys sees it) — check both rather than depend on which is active this run.
    warning = capsys.readouterr().out + caplog.text
    assert "tier_file_missing" in warning
    assert "tier3_final.tsv" in warning


def test_tier4_group_column_collapses_to_a_single_tier_and_tolerates_spaces(tmp_path):
    index = TierIndex.from_dir(_write_tier_files(tmp_path / "core", with_tier4=True))
    # Both `stopword` and `wf10` groups surface as plain `tier4` (D-75) — the group
    # itself is not a separate exported tier.
    assert index.tier_of("the") == "tier4"
    assert index.tier_of("live_people") == "tier4"
    # A `word` column entry with a space is slugified the same way a lexeme_id is.
    assert index.tier_of("to_be") == "tier4"
    assert index.tier_of("to be") == "unknown"


def test_the_tier_column_is_stamped_on_every_grain(tmp_path):
    tiers = _write_tier_files(tmp_path / "core")
    result = _export(
        tmp_path,
        [_rich_entry(), _target_entry(), _entry("marl", [_sense(0, "A lime-rich mudstone.")])],
        tiers_dir=tiers,
    )
    assert result.stats.lexemes_by_tier == {"core": 1, "tier2": 1, "tier3": 1}
    assert {row["lexeme_id"]: row["tier"] for row in _read(result, "lexicon")} == {
        "ridge": "core",
        "crest": "tier2",
        "marl": "tier3",
    }
    assert {row["tier"] for row in _read(result, "definitions")} == {"core", "tier2", "tier3"}
    # A derived repo reads the same tier the store pass stamped.
    assert {row["tier"] for row in _read(result, "pretrain")} <= {"core", "tier2", "tier3"}


# --------------------------------------------------------------------------------------
# Tombstoned relations
# --------------------------------------------------------------------------------------


def test_tombstoned_edges_are_recovered_from_the_reconcile_records(tmp_path):
    entry = _entry("ridge", [_sense(0, "A raised bank.")])
    entry.add_provenance(
        Provenance(
            stage=StageName.HYGIENE,
            model="rule:relation_reconcile",
            prompt_version="7",
            note=(
                "reconcile:tombstone ridge:noun:0\n"
                "reconcile:tombstone: see_also -> banners [demoted: inflection of headword]\n"
                "reconcile:tombstone: see_also -> flags [retyped: nano synonym→see_also]"
            ),
        )
    )
    entry.add_provenance(
        Provenance(
            stage=StageName.HYGIENE,
            model="rule:relation_reconcile",
            prompt_version="7",
            note="reconcile:cap ridge:noun:0\nreconcile:cap:synonym -> rappel [-]",
        )
    )
    result = _export(tmp_path, [entry])
    rows = _read(result, "relations", "tombstoned")
    assert [(row["step"], row["type"], row["target_term"]) for row in rows] == [
        ("tombstone", "see_also", "banners"),
        ("tombstone", "see_also", "flags"),
        ("cap", "synonym", "rappel"),
    ]
    assert rows[0]["edge_id"] == "ridge:noun:0-see_also->banners"
    assert rows[0]["reason"] == "demoted: inflection of headword"
    assert rows[2]["reason"] == "-"
    assert rows[0]["provenance_id"] == "p1"
    assert result.stats.tombstoned_by_step == {"tombstone": 2, "cap": 1}


def test_a_note_that_is_not_a_removal_record_yields_nothing(tmp_path):
    entry = _entry("ridge", [_sense(0, "A raised bank.")])
    entry.add_provenance(
        Provenance(
            stage=StageName.QUERIES,
            model="gpt-5.6-luna",
            prompt_version="7",
            note="queries:ridge:noun:0:abc123;attempts=1",
        )
    )
    result = _export(tmp_path, [entry])
    assert _read(result, "relations", "tombstoned") == []


def test_an_edge_id_is_taken_apart_by_relation_type_not_by_the_last_hyphen():
    assert hf_rows.relation_type_of_edge("well-being:noun:0-synonym->welfare") == (
        "well-being:noun:0",
        "synonym",
        "welfare",
    )
    assert hf_rows.relation_type_of_edge("a:noun:0-confusable_with->b")[1] == "confusable_with"
    assert hf_rows.relation_type_of_edge("not an edge id") == (None, None, None)


# --------------------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------------------


def _front_matter(text: str) -> dict:
    """Parse a card's YAML front matter."""
    yaml = pytest.importorskip("yaml")
    assert text.startswith("---\n")
    _, block, _ = text.split("---\n", 2)
    return yaml.safe_load(block)


def test_every_card_has_valid_front_matter_naming_its_own_config_globs(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    for spec in REPOS:
        text = (hf.repo_dir(result.out_dir, spec) / "README.md").read_text(encoding="utf-8")
        meta = _front_matter(text)
        assert meta["license"] == "cc-by-4.0"
        assert meta["language"] == ["en"]
        assert len(meta["size_categories"]) == 1
        configs = meta["configs"]
        assert [entry["config_name"] for entry in configs] == [c.name for c in spec.configs]
        for entry, config in zip(configs, spec.configs, strict=True):
            assert entry["data_files"] == [{"split": "train", "path": spec.data_glob(config)}]
            files = sorted(hf.data_dir(result.out_dir, spec, config).glob("*.parquet"))
            assert files, f"{spec.name()}/{config.name}"


def test_every_card_states_the_row_count_the_parquet_files_actually_hold(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    for spec in REPOS:
        text = (hf.repo_dir(result.out_dir, spec) / "README.md").read_text(encoding="utf-8")
        for config in spec.configs:
            rows = len(_read(result, spec.slug, config.name))
            assert rows == result.stats.rows[(spec.slug, config.name)]
            assert f"{rows:,} rows, {config.grain}." in text


def test_every_card_documents_every_column_it_writes(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    for spec in REPOS:
        text = (hf.repo_dir(result.out_dir, spec) / "README.md").read_text(encoding="utf-8")
        for config in spec.configs:
            for field in config.fields:
                assert f"| `{field.name}` |" in text, f"{spec.name()}/{config.name}.{field.name}"


def test_every_card_carries_the_family_table_the_citation_and_the_limitations(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    for spec in REPOS:
        text = (hf.repo_dir(result.out_dir, spec) / "README.md").read_text(encoding="utf-8")
        assert "## Related datasets" in text
        assert f"**`{spec.name()}`** (this one)" in text
        for other in REPOS:
            assert other.name() in text
        assert "## Known limitations" in text
        assert "arxiv.org/abs/2511.18622" in text
        assert "Creative Commons Attribution 4.0" in text
        assert spec.snippet_title in text


def test_the_card_example_row_is_a_row_that_was_really_written(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    text = (hf.repo_dir(result.out_dir, REPOS_BY_SLUG["senses"]) / "README.md").read_text(
        encoding="utf-8"
    )
    block = text.split("**One real row:**", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    example = json.loads(block)
    first = _read(result, "senses")[0]
    assert example["sense_id"] == first["sense_id"]
    assert example["gloss"] == first["gloss"]


def test_the_scope_note_compares_against_the_published_v13_figures(tmp_path):
    result = _export(tmp_path, [_rich_entry()])
    text = (hf.repo_dir(result.out_dir, REPOS_BY_SLUG["lexicon"]) / "README.md").read_text(
        encoding="utf-8"
    )
    assert f"{hf_cards.V13.LEXEMES:,}" in text
    assert "not** a superset of v1.3" in text
    assert hf_cards.V13.URL in text


def test_card_renders_with_four_tiers_present(tmp_path):
    tiers = _write_tier_files(tmp_path / "core", with_tier4=True)
    entries = [
        _rich_entry(),  # ridge -> core
        _target_entry(),  # crest -> tier2
        _entry("marl", [_sense(0, "A lime-rich mudstone.")]),  # tier3
        _entry("the", [_sense(0, "Used to refer to a specific thing.")]),  # tier4
    ]
    result = _export(tmp_path, entries, tiers_dir=tiers)
    assert result.stats.tiers_present == ("core", "tier2", "tier3", "tier4")
    text = (hf.repo_dir(result.out_dir, REPOS_BY_SLUG["senses"]) / "README.md").read_text(
        encoding="utf-8"
    )
    assert "| Field | Of | `core` | `tier2` | `tier3` | `tier4` |" in text
    assert "- `core` — top 10K by composite frequency" in text
    assert "- `tier4` — stopwords, plus compounds and names at Wikipedia frequency ≥ 10" in text
    assert "built in 4 frequency-ranked passes (`core`, `tier2`, `tier3` and `tier4`)" in text


def test_card_renders_with_two_tiers_present(tmp_path):
    tiers = _write_tier_files(tmp_path / "core")
    result = _export(tmp_path, [_rich_entry(), _target_entry()], tiers_dir=tiers)
    assert result.stats.tiers_present == ("core", "tier2")
    text = (hf.repo_dir(result.out_dir, REPOS_BY_SLUG["senses"]) / "README.md").read_text(
        encoding="utf-8"
    )
    assert "| Field | Of | `core` | `tier2` |" in text
    assert "built in 2 frequency-ranked passes (`core` and `tier2`)" in text
    assert "- `core` — top 10K by composite frequency" in text
    assert "- `tier3`" not in text
    assert "- `tier4`" not in text


def test_the_coverage_table_reports_per_tier_shares_that_match_the_data(tmp_path):
    tiers = _write_tier_files(tmp_path / "core")
    result = _export(tmp_path, [_rich_entry(), _target_entry()], tiers_dir=tiers)
    stats = result.stats
    # `ridge` (core) has queries; `crest` (tier2) does not.
    assert stats.coverage_share("queries", "sense", "core") == 1.0
    assert stats.coverage_share("queries", "sense", "tier2") == 0.0
    assert stats.coverage_share("etymology", "lexeme", "core") == 1.0
    assert stats.coverage_share("etymology", "lexeme", "tier2") == 0.0
    text = (hf.repo_dir(result.out_dir, REPOS_BY_SLUG["senses"]) / "README.md").read_text(
        encoding="utf-8"
    )
    assert "| Synthetic retrieval queries | sense | 100.0% | 0.0% |" in text


def test_size_categories_track_the_row_count():
    assert hf_cards._size_category(10) == "n<1K"
    assert hf_cards._size_category(5_000) == "1K<n<10K"
    assert hf_cards._size_category(2_000_000) == "1M<n<10M"
    assert hf_cards._size_category(10**9) == "100M<n<1B"


# --------------------------------------------------------------------------------------
# Derived repos
# --------------------------------------------------------------------------------------


def test_qrels_writes_the_trec_file_beside_its_parquet_configs(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    trec = hf.repo_dir(result.out_dir, REPOS_BY_SLUG["qrels"]) / "qrels.trec"
    lines = [line.split() for line in trec.read_text(encoding="utf-8").splitlines()]
    assert lines
    assert all(len(line) == 4 and line[1] == "0" for line in lines)
    query_ids = {row["query_id"] for row in _read(result, "qrels", "listwise")}
    assert {line[0] for line in lines} <= query_ids
    doc_ids = {row["doc_id"] for row in _read(result, "qrels", "docs")}
    assert {line[2] for line in lines} <= doc_ids


def test_retrieval_pairs_split_spans_into_two_columns(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    rows = _read(result, "retrieval-pairs")
    assert rows
    positive = next(row for row in rows if row["kind"] == "wic_positive")
    assert positive["label"] == 1
    assert positive["span_a_start"] == 4
    assert positive["span_a_end"] == 9
    assert positive["lexeme_id"] == "ridge"


def test_derived_summaries_reach_the_cards(tmp_path):
    result = _export(tmp_path, [_rich_entry(), _target_entry()])
    assert set(result.stats.derived_summaries) == {
        "retrieval-pairs",
        "retrieval-triples",
        "qrels",
        "pretrain",
    }
    text = (
        hf.repo_dir(result.out_dir, REPOS_BY_SLUG["retrieval-triples"]) / "README.md"
    ).read_text(encoding="utf-8")
    assert "`triples_written`" in text


# --------------------------------------------------------------------------------------
# Push
# --------------------------------------------------------------------------------------


class _FakeApi:
    """Records what ``push_repos`` would do, so the upload path is tested offline."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.uploaded: list[dict] = []

    def create_repo(self, **kwargs: object) -> None:
        self.created.append(kwargs)

    def upload_large_folder(self, **kwargs: object) -> None:
        self.uploaded.append(kwargs)


def test_push_creates_each_repo_then_uploads_its_folder(tmp_path):
    result = _export(tmp_path, [_rich_entry()], repos="senses,queries")
    api = _FakeApi()
    pushed = hf.push_repos(
        result.out_dir, resolve_repos("senses,queries"), owner="acme", private=True, api=api
    )
    assert [call["repo_id"] for call in api.created] == [
        "acme/opengloss-v2.1-senses",
        "acme/opengloss-v2.1-queries",
    ]
    assert all(call["repo_type"] == "dataset" for call in api.created)
    assert all(call["private"] is True for call in api.created)
    assert all(call["exist_ok"] is True for call in api.created)
    assert [Path(call["folder_path"]).name for call in api.uploaded] == [
        "opengloss-v2.1-senses",
        "opengloss-v2.1-queries",
    ]
    assert pushed[0]["url"] == "https://huggingface.co/datasets/acme/opengloss-v2.1-senses"


def test_export_does_not_push_by_itself(tmp_path):
    api = _FakeApi()
    _export(tmp_path, [_rich_entry()])
    assert api.created == []
    assert api.uploaded == []


# --------------------------------------------------------------------------------------
# Store safety
# --------------------------------------------------------------------------------------


def test_the_export_never_writes_to_the_store(tmp_path):
    store = _store(tmp_path)
    store.write(_rich_entry())
    before = {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))}
    hf.export_hf(store, tmp_path / "out")
    after = {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))}
    assert before == after


def test_the_schemas_module_names_every_repo_exactly_once():
    assert set(hf_schemas.STORE_REPO_SLUGS) | set(hf_schemas.DERIVED_REPO_SLUGS) == set(
        hf_schemas.ALL_REPO_SLUGS
    )
    assert not hf_schemas.STORE_REPO_SLUGS & hf_schemas.DERIVED_REPO_SLUGS
