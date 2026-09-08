"""`scripts/clean_tier6_seeds.py` (D-83): the tier-6 seed cleaner.

Loaded via `importlib` (the same pattern `test_prompts.py`'s reload test uses for a
module outside `src/`, since `scripts/` carries no `__init__.py` and is not on the
normal import path). The retype call itself is exercised through a `FunctionModel`
(`conftest.py`'s own convention for a `NativeOutput` stage: the payload comes back as a
`TextPart` of JSON, not a tool call), so the whole suite stays offline and free.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import EntityType
from opengloss_generator.seed_list import read_seed_list
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag


def _load_module() -> types.ModuleType:
    """Import `scripts/clean_tier6_seeds.py` as a module, independent of `sys.path`."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "clean_tier6_seeds.py"
    spec = importlib.util.spec_from_file_location("clean_tier6_seeds", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load a module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load_module()

TIER6_HEADER = (
    "name\tword\tentity_type\tsource\tsources\timportance_score\tvital_level\tsitelinks\t"
    "in_wordnet\tin_v13\tin_store\tstore_slug\tqid\tscore_terms\tnotes"
)


def _row(
    name: str,
    entity_type: str,
    *,
    source: str = "name_seed",
    qid: str = "Q1",
    notes: str = "",
) -> str:
    """Return one tier-6 TSV line, matching `test_generate_seeded.py`'s own helper."""
    cells = [name, name.lower(), entity_type, source, "wikipedia_vital", "80.0", "4", "100"]
    cells += ["0", "0", "0", name.lower().replace(" ", "_"), qid, "vital4=60", notes]
    return "\t".join(cells)


# --------------------------------------------------------------------------------------
# Step 1 -- disambiguator stripping
# --------------------------------------------------------------------------------------


def test_strip_disambiguator_splits_a_trailing_parenthetical():
    assert m.strip_disambiguator("Bonnie and Clyde (film)") == ("Bonnie and Clyde", "film")


def test_strip_disambiguator_keeps_a_multi_word_disambiguator_whole():
    assert m.strip_disambiguator("Georgia (U.S. state)") == ("Georgia", "U.S. state")


def test_strip_disambiguator_is_a_no_op_with_no_parenthetical():
    assert m.strip_disambiguator("Denver") == ("Denver", "")


def test_strip_disambiguator_does_not_split_a_parenthetical_only_name():
    # A headword cannot be empty; this is not a real disambiguator.
    assert m.strip_disambiguator("(untitled)") == ("(untitled)", "")


def test_strip_disambiguator_ignores_a_non_trailing_parenthetical():
    # Only a *trailing* parenthetical is a Wikipedia disambiguator.
    headword, disambiguator = m.strip_disambiguator("A(B) C")
    assert headword == "A(B) C"
    assert disambiguator == ""


# --------------------------------------------------------------------------------------
# Step 3 -- the mapping tables
# --------------------------------------------------------------------------------------


def test_hypernym_from_label_picks_the_first_usable_label():
    assert m.hypernym_from_label(["human", "city"]) == "city"


def test_hypernym_from_label_skips_stoplisted_labels():
    assert m.hypernym_from_label(["human", "entity"]) is None


def test_hypernym_from_label_empty_list_is_none():
    assert m.hypernym_from_label([]) is None


def test_domain_hint_from_label_maps_a_settlement_to_the_settlements_leaf():
    assert m.domain_hint_from_label(["city"]) == DomainTag.NATURE_SETTLEMENTS.value


def test_domain_hint_from_label_maps_a_polity_to_the_polities_leaf():
    assert m.domain_hint_from_label(["sovereign state"]) == DomainTag.LAW_GOVERNMENT_POLITIES.value


def test_domain_hint_from_label_is_none_for_an_unrecognised_label():
    # D-82/D-81: no per-type default is safe for organization or place, so an unmatched
    # label must come back blank, not a guess.
    assert m.domain_hint_from_label(["human"]) is None


def test_domain_hint_from_label_maps_church_to_religion_not_business():
    # The specific failure D-82 found: `organization -> business.general` mistagged the
    # Church of England.
    assert m.domain_hint_from_label(["church"]) == DomainTag.HUMANITIES_RELIGION.value


def test_occupation_domain_government_office_outranks_an_incidental_occupation():
    # Live Wikidata order for John F. Kennedy's P106 (2026-09-08): "writer" first,
    # "politician" second. List order must not decide a president's domain.
    labels = ["writer", "politician", "statesperson", "journalist"]
    assert m.occupation_domain(labels) == DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value


def test_occupation_domain_government_office_outranks_a_judge_credential():
    # Harry S. Truman's P106: "judge" first, "politician" fourth.
    labels = ["judge", "captain", "businessperson", "politician"]
    assert m.occupation_domain(labels) == DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value


def test_occupation_domain_scientist_with_no_office_gets_science():
    assert m.occupation_domain(["physicist"]) == DomainTag.SCIENCE_GENERAL.value


def test_occupation_domain_unrecognised_occupation_is_none():
    assert m.occupation_domain(["florist"]) is None


def test_event_domain_world_war_year_maps_to_world_wars():
    assert (
        m.event_domain("Battle of Midway (1942)", ["battle"]) == DomainTag.HISTORY_WORLD_WARS.value
    )


def test_event_domain_battle_outside_world_war_years_maps_to_modern_history():
    assert (
        m.event_domain("Battle of Gettysburg (1863)", ["battle"])
        == DomainTag.HISTORY_MODERN_HISTORY.value
    )


def test_event_domain_non_battle_label_falls_through_to_the_label_table():
    assert (
        m.event_domain("2020 United States election", ["election"])
        == DomainTag.LAW_GOVERNMENT_ELECTIONS_POLITICS.value
    )


def test_event_domain_unrecognised_label_is_none():
    assert m.event_domain("Some Happening", ["unrecognised class"]) is None


def test_resolve_hypernym_and_domain_dropped_row_is_blank():
    hints = m.TypedHints(
        entity_type_final=None,
        type_source="verdict",
        dropped_reason="not_a_named_entity",
        p31_labels=[],
    )
    assert m.resolve_hypernym_and_domain(headword="X", notes="", hints=hints) == ("", "")


def test_resolve_hypernym_and_domain_organization_with_no_signal_has_no_domain():
    hints = m.TypedHints(
        entity_type_final=EntityType.ORGANIZATION,
        type_source="tsv",
        dropped_reason="",
        p31_labels=[],
    )
    hypernym, domain = m.resolve_hypernym_and_domain(headword="Acme", notes="", hints=hints)
    assert domain == ""
    assert hypernym  # falls back to seed_list.hypernym_for's crude default


def test_resolve_hypernym_and_domain_organization_with_church_label_gets_religion():
    hints = m.TypedHints(
        entity_type_final=EntityType.ORGANIZATION,
        type_source="tsv",
        dropped_reason="",
        p31_labels=["church"],
    )
    hypernym, domain = m.resolve_hypernym_and_domain(
        headword="Church of England", notes="", hints=hints
    )
    assert hypernym == "church"
    assert domain == DomainTag.HUMANITIES_RELIGION.value


def test_resolve_hypernym_and_domain_person_with_no_signal_gets_historical_figures():
    hints = m.TypedHints(
        entity_type_final=EntityType.PERSON,
        type_source="tsv",
        dropped_reason="",
        p31_labels=[],
        occupation_labels=[],
    )
    _, domain = m.resolve_hypernym_and_domain(headword="Someone", notes="", hints=hints)
    assert domain == DomainTag.HISTORY_HISTORICAL_FIGURES.value


def test_resolve_hypernym_and_domain_person_occupation_refines_the_default():
    hints = m.TypedHints(
        entity_type_final=EntityType.PERSON,
        type_source="tsv",
        dropped_reason="",
        p31_labels=[],
        occupation_labels=["physicist"],
    )
    _, domain = m.resolve_hypernym_and_domain(headword="Someone", notes="", hints=hints)
    assert domain == DomainTag.SCIENCE_GENERAL.value


# --------------------------------------------------------------------------------------
# Wikidata-derived retyping (free half)
# --------------------------------------------------------------------------------------


def test_candidate_wikidata_type_recomputes_from_p31_classes():
    wd_facts = {"Q1": {"p31": ["QCLASS"]}}
    class_types = {"QCLASS": ["place"]}
    assert m.candidate_wikidata_type("Q1", wd_facts, class_types) is EntityType.PLACE


def test_candidate_wikidata_type_reject_marker_gives_none():
    wd_facts = {"Q1": {"p31": ["QCLASS"]}}
    class_types = {"QCLASS": ["DISAMBIG"]}
    assert m.candidate_wikidata_type("Q1", wd_facts, class_types) is None


def test_candidate_wikidata_type_absent_qid_gives_none():
    assert m.candidate_wikidata_type("Q999", {}, {}) is None


def test_candidate_wikidata_type_person_beats_place_in_priority_order():
    # A biography with a birthplace's class also attached should still resolve to
    # `person` -- the same priority `TYPE_PRIORITY`/`CLASS_TOPS` use at build time.
    wd_facts = {"Q1": {"p31": ["QHUMAN", "QCITY"]}}
    class_types = {"QHUMAN": ["person"], "QCITY": ["place"]}
    assert m.candidate_wikidata_type("Q1", wd_facts, class_types) is EntityType.PERSON


# --------------------------------------------------------------------------------------
# Collision notes
# --------------------------------------------------------------------------------------


def _write_and_read(tmp_path: Path, lines: list[str]) -> list[Any]:
    """Write ``lines`` as a TSV and return `prepare_headwords`-ready rows for it."""
    path = tmp_path / "candidates.tsv"
    path.write_text("\n".join(lines), encoding="utf-8")
    _, rows = m.read_tsv(path)
    return rows


def test_annotate_collisions_notes_a_store_collision(tmp_path: Path):
    store = LexemeStore(StoreConfig(root=tmp_path / "store"))
    store.path_for("denver").parent.mkdir(parents=True, exist_ok=True)
    store.path_for("denver").write_text("{}", encoding="utf-8")

    rows = _write_and_read(tmp_path, [TIER6_HEADER, _row("Denver", "place")])
    m.prepare_headwords(rows, store)
    assert "headword_collision: store has 'denver'" in rows[0].cells["notes"]


def test_annotate_collisions_notes_an_intra_file_collision_on_both_rows(tmp_path: Path):
    store = LexemeStore(StoreConfig(root=tmp_path / "store"))
    rows = _write_and_read(
        tmp_path,
        [TIER6_HEADER, _row("Julius Caesar", "person"), _row("Julius Caesar (play)", "work")],
    )
    m.prepare_headwords(rows, store)
    assert "headword_collision: also produced by" in rows[0].cells["notes"]
    assert "headword_collision: also produced by" in rows[1].cells["notes"]


def test_annotate_collisions_adds_no_note_with_no_collision(tmp_path: Path):
    store = LexemeStore(StoreConfig(root=tmp_path / "store"))
    rows = _write_and_read(tmp_path, [TIER6_HEADER, _row("Zzyzx", "place")])
    m.prepare_headwords(rows, store)
    assert "headword_collision" not in rows[0].cells["notes"]


# --------------------------------------------------------------------------------------
# TSV I/O and the seed_list.read_seed_list round trip
# --------------------------------------------------------------------------------------


def test_tsv_round_trip_preserves_cells(tmp_path: Path):
    path = tmp_path / "candidates.tsv"
    path.write_text(
        "\n".join([TIER6_HEADER, _row("Denver", "place"), _row("Abraham Lincoln", "person")]),
        encoding="utf-8",
    )
    header, rows = m.read_tsv(path)
    out_header = m.build_output_header(header)
    for column in m.NEW_COLUMNS:
        for row in rows:
            row.cells.setdefault(column, "")
    out_path = tmp_path / "out.tsv"
    m.write_tsv(out_path, out_header, rows)

    reread_header, reread_rows = m.read_tsv(out_path)
    assert reread_header == out_header
    assert [r.cells["name"] for r in reread_rows] == ["Denver", "Abraham Lincoln"]
    assert all(r.cells[column] == "" for r in reread_rows for column in m.NEW_COLUMNS)


def test_read_tsv_missing_required_column_raises(tmp_path: Path):
    path = tmp_path / "bad.tsv"
    path.write_text("name\tword\tsource\nDenver\tdenver\tname_seed", encoding="utf-8")
    with pytest.raises(ValueError, match="entity_type"):
        m.read_tsv(path)


def _last_user_text(messages: Sequence[ModelMessage]) -> str:
    """Return the text of the most recent user prompt (mirrors `conftest.py`)."""
    for message in reversed(list(messages)):
        for part in getattr(message, "parts", []):
            if part.part_kind == "user-prompt":
                return str(part.content)
    return ""


def _function_model(verdict_by_name: dict[str, str]) -> FunctionModel:
    """Return a `FunctionModel` answering a retype batch from a fixed name->type map.

    Mirrors `conftest.py`'s own `scripted_model`: `NativeOutput` asks for a JSON *text*
    response (`output_object`), not a tool call, so the reply is a `TextPart`.
    """

    def respond(messages: Sequence[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _last_user_text(messages)
        names = [line.split(". ", 1)[1].split(" — ")[0] for line in prompt.splitlines()[1:]]
        payload = {
            "verdicts": [
                {"term": name, "entity_type": verdict_by_name.get(name, "not_a_named_entity")}
                for name in names
            ]
        }
        return ModelResponse(
            parts=[TextPart(json.dumps(payload))],
            usage=RequestUsage(input_tokens=100, output_tokens=40),
            model_name="scripted",
        )

    return FunctionModel(respond)


def test_main_output_is_consumable_by_read_seed_list(tmp_path: Path, monkeypatch):
    candidates = tmp_path / "candidates.tsv"
    candidates.write_text(
        "\n".join(
            [
                TIER6_HEADER,
                _row("Bonnie and Clyde (film)", "work", qid="Q1"),
                _row("Arianism", "place", qid="Q2"),  # a mistyped doctrine
                _row("Abraham Lincoln", "person", source="wordnet", qid="Q91"),
            ]
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in ("wd_facts", "class_types", "vital_all", "class_labels", "occupation_labels"):
        (cache / f"{name}.json").write_text("{}", encoding="utf-8")
    store = tmp_path / "store"
    out = tmp_path / "seeds.tsv"

    agent = m.Agent(
        _function_model({"Bonnie and Clyde": "work", "Arianism": "not_a_named_entity"}),
        output_type=m.NativeOutput(m.RetypeBatch, strict=True),
        instructions=m.RETYPE_INSTRUCTIONS,
        retries=0,
    )
    monkeypatch.setattr(m, "build_retype_agent", lambda: agent)

    exit_code = m.main(
        [
            "--candidates",
            str(candidates),
            "--cache",
            str(cache),
            "--store",
            str(store),
            "--out",
            str(out),
            "--budget",
            "1.0",
            "--offline",
            "--no-occupations",
        ]
    )
    assert exit_code == 0

    seeds = read_seed_list(out)
    by_name = {row.name: row for row in seeds}
    # The disambiguator was stripped, and the row survives with its final type.
    assert "Bonnie and Clyde" in by_name
    assert by_name["Bonnie and Clyde"].entity_type is EntityType.WORK
    # `not_a_named_entity` is not a member of EntityType, so read_seed_list already
    # skips it with no reader change (module docstring's whole point).
    assert "Arianism" not in by_name
    # A wordnet row is not retyped and is not readable by the name_seed filter, but it
    # survives untouched in the file itself.
    _, rows = m.read_tsv(out)
    wordnet_row = next(r for r in rows if r.cells["source"] == "wordnet")
    assert wordnet_row.cells["name"] == "Abraham Lincoln"
    assert wordnet_row.cells["entity_type_final"] == "person"
    assert wordnet_row.cells["type_source"] == "tsv"
    assert wordnet_row.cells["dropped_reason"] == ""
