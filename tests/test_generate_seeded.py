"""D-82: the seeded `generate` path — a name and a type instead of an overview call."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opengloss_generator import prompts
from opengloss_generator.schema import EntityType, LexemeKind, PartOfSpeech
from opengloss_generator.seed_list import (
    SeedRow,
    domain_hint_for,
    hypernym_for,
    read_seed_list,
)
from opengloss_generator.taxonomy import DomainTag
from opengloss_generator.workflows.generate import EntrySpec, generate_entry, seeded_overview

TIER6_HEADER = (
    "name\tword\tentity_type\tsource\tsources\timportance_score\tvital_level\tsitelinks\t"
    "in_wordnet\tin_v13\tin_store\tstore_slug\tqid\tscore_terms\tnotes"
)


def _row(
    name: str,
    entity_type: str,
    source: str = "name_seed",
    qid: str = "Q1",
    notes: str = "",
) -> str:
    """Return one tier-6 TSV line with the columns this path reads filled in."""
    cells = [name, name.lower(), entity_type, source, "wikipedia_vital", "80.0", "4", "100"]
    cells += ["0", "0", "0", name.lower().replace(" ", "_"), qid, "vital4=60", notes]
    return "\t".join(cells)


def _seeded_spec(**overrides: object) -> EntrySpec:
    """Return a spec seeded the way `--seed-list` seeds one."""
    fields: dict[str, object] = {
        "headword": "Denver",
        "kind": LexemeKind.PROPER_NOUN,
        "entity_type": EntityType.PLACE,
        "wikidata_qid": "Q16554",
        "hypernym": "city",
        "domain": "nature.settlements",
    }
    fields.update(overrides)
    return EntrySpec(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# The skip itself
# --------------------------------------------------------------------------------------


async def test_seeded_spec_skips_the_overview_stage(session):
    result = await generate_entry(_seeded_spec(), session.stages)

    stages = {p.stage.value for p in result.entry.provenance.values()}
    assert "overview" not in stages
    assert "overview" not in session.meter.summary().by_stage
    # senses + spans + the three long-form sections; the overview call is the one saved.
    assert "senses" in stages


async def test_seeded_spec_writes_the_kind_entity_type_and_qid(session):
    result = await generate_entry(_seeded_spec(), session.stages)
    entry = result.entry

    assert entry.lexeme_id == "denver"
    assert entry.headword == "Denver"
    assert entry.kind is LexemeKind.PROPER_NOUN
    assert entry.proper_noun is not None
    assert entry.proper_noun.entity_type is EntityType.PLACE
    assert entry.proper_noun.wikidata_qid == "Q16554"


async def test_a_seeded_name_gets_one_noun_sense(session):
    result = await generate_entry(_seeded_spec(), session.stages)
    assert [pos_entry.pos for pos_entry in result.entry.pos_entries] == [PartOfSpeech.NOUN]
    assert result.entry.sense_count() == 1


async def test_seeded_entry_gets_the_same_sections_generate_writes_today(session):
    # NAMED-ENTITY-PLAN § 4f: this path creates the entry and nothing else. No register
    # rendition and no contrast is produced here; the chain owns those.
    result = await generate_entry(_seeded_spec(), session.stages)
    entry = result.entry

    assert entry.etymology is not None
    assert entry.encyclopedia.canonical() is not None
    assert entry.lexical_explanation.canonical() is not None
    for _, sense, _ in entry.iter_senses():
        assert [(r.reading_level, r.style) for r in sense.gloss] == [
            (r.reading_level, r.style) for r in sense.gloss[:1]
        ]
        assert len(sense.gloss) == 1
    assert entry.contrasts == []


async def test_an_unseeded_spec_still_calls_the_overview(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)

    stages = {p.stage.value for p in result.entry.provenance.values()}
    assert "overview" in stages
    assert "overview" in session.meter.summary().by_stage
    assert result.entry.kind is LexemeKind.SIMPLEX


async def test_a_half_seeded_spec_takes_the_ordinary_path(session):
    # A kind with no entity type cannot write the plan, so it is not a seed.
    spec = EntrySpec(headword="Einstein", kind=LexemeKind.PROPER_NOUN)
    assert seeded_overview(spec) is None
    result = await generate_entry(spec, session.stages)
    assert "overview" in {p.stage.value for p in result.entry.provenance.values()}


def test_an_unusable_qid_is_dropped_rather_than_failing_the_entry():
    plan = seeded_overview(_seeded_spec(wikidata_qid="not-a-qid"))
    assert plan is not None
    assert plan.proper_noun is not None
    assert plan.proper_noun.wikidata_qid is None


# --------------------------------------------------------------------------------------
# What the senses call is told
# --------------------------------------------------------------------------------------


async def test_the_seed_reaches_the_senses_prompt(session, monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    original = prompts.build_senses_prompt

    def record(*args: object, **kwargs: object) -> str:
        built = original(*args, **kwargs)  # type: ignore[arg-type]
        seen.append(built)
        return built

    monkeypatch.setattr(prompts, "build_senses_prompt", record)
    await generate_entry(_seeded_spec(), session.stages)

    assert len(seen) == 1
    prompt = seen[0]
    assert "it is a proper noun naming a place" in prompt
    assert "Denver is a city" in prompt
    assert "Domain hint: nature.settlements" in prompt
    assert "open the gloss with the name itself" in prompt


async def test_an_unseeded_entry_is_told_nothing_new(session, monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    original = prompts.build_senses_prompt

    def record(*args: object, **kwargs: object) -> str:
        built = original(*args, **kwargs)  # type: ignore[arg-type]
        seen.append(built)
        return built

    monkeypatch.setattr(prompts, "build_senses_prompt", record)
    # "Einstein" is scripted as a proper noun in the overview, but nothing seeded it, so
    # the block must not appear: there is no source behind that judgement to quote.
    await generate_entry(EntrySpec(headword="Einstein"), session.stages)

    assert seen
    assert all("already known" not in prompt for prompt in seen)


def test_the_known_block_names_the_type_in_words_and_the_hypernym():
    block = prompts.build_known_entity_block("Yellowstone", "place", "national park")
    assert "it is a proper noun naming a place" in block
    assert "Yellowstone is a national park" in block


def test_the_known_block_survives_a_missing_hypernym():
    block = prompts.build_known_entity_block("Cyrillic script", "other")
    assert "a named thing" in block
    assert "is a None" not in block


# --------------------------------------------------------------------------------------
# Byte stability (D-25): the volatile block must not have leaked into the instructions
# --------------------------------------------------------------------------------------


def test_senses_instructions_are_byte_stable_and_the_version_is_unchanged():
    spec = importlib.util.spec_from_file_location("prompts_reloaded", Path(prompts.__file__))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SENSES_INSTRUCTIONS == prompts.SENSES_INSTRUCTIONS
    # The seed block is volatile content appended to the per-call prompt, so the stable
    # half did not change and PROMPT_VERSION must not move (D-25).
    assert module.PROMPT_VERSION == prompts.PROMPT_VERSION == "8"


def test_the_seed_block_is_not_in_the_cached_instructions():
    assert "already known" not in prompts.SENSES_INSTRUCTIONS
    assert "Denver" not in prompts.SENSES_INSTRUCTIONS
    built = prompts.build_senses_prompt("Denver", "noun", 1, entity_type="place", hypernym="city")
    assert built.startswith("Headword: Denver")
    assert prompts.build_senses_prompt("Denver", "noun", 1) == "\n".join(
        ["Headword: Denver", "Part of speech: noun", "Number of senses to write: 1"]
    )


# --------------------------------------------------------------------------------------
# The seed-list reader
# --------------------------------------------------------------------------------------


def test_reader_filters_by_source_and_preserves_display_case(tmp_path: Path):
    path = tmp_path / "tier6.tsv"
    path.write_text(
        "\n".join(
            [
                TIER6_HEADER,
                _row("John F. Kennedy", "person", qid="Q9696", notes="us_list=us_president"),
                _row("abalone", "other", source="wordnet"),
                _row("Yellowstone National Park", "place", qid="Q351363"),
            ]
        ),
        encoding="utf-8",
    )

    rows = read_seed_list(path)
    assert [row.name for row in rows] == ["John F. Kennedy", "Yellowstone National Park"]
    assert rows[0].entity_type is EntityType.PERSON
    assert rows[0].wikidata_qid == "Q9696"
    assert rows[0].hypernym == "president of the United States"

    everything = read_seed_list(path, source=None)
    assert len(everything) == 3
    assert read_seed_list(path, source="wordnet")[0].name == "abalone"


def test_reader_drops_duplicates_and_untyped_rows(tmp_path: Path):
    path = tmp_path / "tier6.tsv"
    path.write_text(
        "\n".join(
            [
                TIER6_HEADER,
                _row("Denver", "place"),
                _row("Denver", "place"),
                _row("Mystery", "spaceship"),
                _row("Blank", ""),
            ]
        ),
        encoding="utf-8",
    )
    assert [row.name for row in read_seed_list(path)] == ["Denver"]


def test_reader_rejects_a_file_it_cannot_read(tmp_path: Path):
    no_header = tmp_path / "bare.tsv"
    no_header.write_text("Denver\tplace\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"no 'name' column"):
        read_seed_list(no_header, source=None)

    no_source = tmp_path / "nosource.tsv"
    no_source.write_text("name\tentity_type\nDenver\tplace\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source"):
        read_seed_list(no_source)
    assert read_seed_list(no_source, source=None) == [
        SeedRow(
            name="Denver",
            entity_type=EntityType.PLACE,
            wikidata_qid=None,
            hypernym="place",
            domain_hint=DomainTag.NATURE_LANDFORMS.value,
        )
    ]


def test_reader_handles_an_empty_file(tmp_path: Path):
    empty = tmp_path / "empty.tsv"
    empty.write_text("", encoding="utf-8")
    assert read_seed_list(empty, source=None) == []


# --------------------------------------------------------------------------------------
# The derived fields
# --------------------------------------------------------------------------------------


def test_hypernym_defaults_to_the_type_and_sharpens_on_a_us_list_token():
    assert hypernym_for(EntityType.PERSON) == "person"
    assert hypernym_for(EntityType.WORK) == "work"
    assert hypernym_for(EntityType.OTHER) == "named entity"
    assert hypernym_for(EntityType.PLACE, "in_store; us_list=us_city_100k") == "city"
    assert hypernym_for(EntityType.PLACE, "us_list=world_country") == "country"
    assert hypernym_for(EntityType.ORGANIZATION, "us_list=us_university") == "university"
    # Several tokens: the first match in the table's order wins.
    assert (
        hypernym_for(EntityType.PERSON, "us_list=us_vice_president,us_president")
        == "president of the United States"
    )


def test_domain_hint_falls_back_when_the_taxonomy_lacks_the_proposed_leaf():
    assert domain_hint_for(EntityType.PERSON) == DomainTag.HISTORY_HISTORICAL_FIGURES.value
    assert domain_hint_for(EntityType.WORK) == DomainTag.ARTS_GENERAL.value

    settlement = domain_hint_for(EntityType.PLACE, "us_list=us_city_100k")
    polity = domain_hint_for(EntityType.PLACE, "us_list=world_country")
    for hint, proposed, fallback in (
        (settlement, "nature.settlements", DomainTag.PEOPLE_SOCIETY_COMMUNITY_LIFE),
        (polity, "law_government.polities", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE),
    ):
        expected = proposed if proposed in set(DomainTag) else fallback.value
        assert hint == expected
    # Whatever the enum currently holds, the hint is always a leaf it can parse.
    assert DomainTag(settlement)
    assert DomainTag(polity)
