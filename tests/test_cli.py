"""Acceptance criteria driven through the CLI (REQUIREMENTS.md § 5)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.runner import RunSession

runner = CliRunner()


_REPO_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, scripted_model, tmp_path: Path) -> Iterator[None]:
    """Route every CLI-constructed session through the scripted model.

    Also isolates run output to `tmp_path`: `AppConfig.log_dir` defaults to `runs`,
    relative to the process's cwd, and every CLI invocation writes a ledger and a log
    file there. Without this, running the CLI suite litters the repository's `runs/`
    with a ledger/log pair per test. `OPENGLOSS_LOG_DIR` is the same override a real
    invocation would use (env vars sit above the built-in default in `load_config`'s
    precedence), so it reaches `RunSession` through the normal config path rather than
    a CLI flag `_build_config` does not expose.
    """
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    before = set(_REPO_RUNS_DIR.iterdir()) if _REPO_RUNS_DIR.is_dir() else set()

    original = RunSession.__init__

    def patched(
        self, config, *, model_override=None, run_id=None, install_signal_handler=False
    ) -> None:
        original(
            self,
            config,
            model_override=scripted_model,
            run_id=run_id,
            install_signal_handler=False,
        )

    monkeypatch.setattr(RunSession, "__init__", patched)

    yield

    after = set(_REPO_RUNS_DIR.iterdir()) if _REPO_RUNS_DIR.is_dir() else set()
    assert after == before, f"test wrote files under the repo's runs/ directory: {after - before}"


def _invoke(*args: str) -> dict:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    # Structured logs go to stderr; only stdout carries the JSON summary.
    return json.loads(result.stdout)


def test_generate_then_show(tmp_path: Path):
    store = str(tmp_path / "store")
    summary = _invoke("generate", "--headword", "abseil", "--store", store, "--budget", "1")
    assert summary["written"] is True
    assert summary["cost_usd"] > 0
    assert summary["senses"] == 3

    shown = _invoke("show", "--headword", "abseil", "--store", store)
    assert shown["lexeme_id"] == "abseil"
    # v3: a sense's definition is its canonical gloss rendition; enrichment adds more.
    glosses = shown["pos_entries"][0]["senses"][0]["gloss"]
    assert len(glosses) == 1
    assert glosses[0]["reading_level"] == "neutral"

    edges = runner.invoke(cli.app, ["show", "--headword", "abseil", "--store", store, "--edges"])
    assert edges.exit_code == 0
    assert json.loads(edges.stdout)[0]["edge_id"].startswith("abseil:")


def test_generate_refuses_to_overwrite_without_force(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    again = _invoke("generate", "--headword", "abseil", "--store", store)
    assert again["stop_reason"] == "already_exists"
    assert again["cost_usd"] == 0


def test_dry_run_costs_nothing(tmp_path: Path):
    summary = _invoke("generate", "--headword", "abseil", "--store", str(tmp_path), "--dry-run")
    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0


def test_walk_from_seed(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    summary = _invoke("walk", "--seed", "abseil", "--max-new", "5", "--store", store)
    assert 0 < summary["generated_count"] <= 5
    assert summary["stop_reason"] in {"max_new_entries", "frontier_exhausted"}


def test_enrich_reading_levels_is_idempotent(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)

    first = _invoke(
        "enrich",
        "--headword",
        "abseil",
        "--reading-levels",
        "grade_1,grade_5,grade_10,college",
        "--store",
        store,
    )
    assert first["changed"] is True
    assert first["renditions_added"] == 12  # 3 senses x 4 levels
    assert first["cost_usd"] > 0

    second = _invoke(
        "enrich",
        "--headword",
        "abseil",
        "--reading-levels",
        "grade_1,grade_5,grade_10,college",
        "--store",
        store,
    )
    assert second["changed"] is False
    assert second["cost_usd"] == 0


def test_enrich_registers(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    summary = _invoke(
        "enrich",
        "--headword",
        "abseil",
        "--registers",
        "informal,technical,formal,marketing",
        "--store",
        store,
    )
    assert summary["renditions_added"] == 12
    stats = _invoke("stats", "--store", store)
    assert stats["renditions"] == 12
    assert set(stats["renditions_by_target"]) == {
        "neutral/informal",
        "neutral/technical",
        "neutral/formal",
        "neutral/marketing",
    }


def test_enrich_missing_entry_is_clean_failure(tmp_path: Path):
    summary = _invoke(
        "enrich", "--headword", "ghost", "--store", str(tmp_path), "--registers", "informal"
    )
    assert summary["stop_reason"] == "not_found"


def test_price_command_lists_every_stage():
    summary = _invoke("price")
    assert summary["default_tier"] == "flex"
    assert set(summary["policies"]) >= {
        "overview",
        "senses",
        "renditions",
        "classify_kind",
        "tag_domain",
        "resolve",
        "spans",
        "qa",
    }
    assert summary["policies"]["senses"]["input_usd_per_mtok"] == pytest.approx(0.10)


def test_enrich_fields_examples(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    summary = _invoke(
        "enrich",
        "--headword",
        "abseil",
        "--fields",
        "examples",
        "--reading-levels",
        "grade_1,grade_5",
        "--store",
        store,
    )
    assert summary["changed"] is True
    assert summary["renditions_added"] == 6  # 3 senses x 2 levels
    assert summary["cost_usd"] > 0

    shown = _invoke("show", "--headword", "abseil", "--store", store)
    example_renditions = shown["pos_entries"][0]["senses"][0]["examples"]
    # The scripted senses stage writes two canonical (neutral/plain) examples per sense;
    # enrich adds the two new levels on top of those.
    assert len(example_renditions) == 4


def test_walk_domain_deficit_strategy(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    summary = _invoke("walk", "--strategy", "domain-deficit", "--max-new", "3", "--store", store)
    assert 0 < summary["generated_count"] <= 3
    # Every sense the scripted senses stage writes is tagged "education.general", so
    # that root is the only one with any candidate to seed from.
    assert "education" in summary["domain_deficit"]
    assert "untagged" in summary["domain_deficit"]


_V13_GREEN_HOUSE = {
    "word": "green house",
    "language": "en",
    "entries": [
        {
            "pos": "noun",
            "senses": [
                {
                    "definition": "A structure for growing plants in a controlled climate.",
                    "synonyms": ["warm room"],
                    "examples": ["The green house stayed warm all winter."],
                }
            ],
        }
    ],
    "tags": ["domain:not_a_real_domain"],
}

_V13_WARM_ROOM = {
    "word": "warm room",
    "language": "en",
    "entries": [
        {
            "pos": "noun",
            "senses": [
                {
                    "definition": "A room kept warm for plants or people.",
                    "examples": ["The warm room protected seedlings from frost."],
                }
            ],
        }
    ],
}


def _write_v13_fixtures(directory: Path) -> None:
    """Write the two small v1.3 payloads used by the migrate/retrofit/resolve test."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "green_house.json").write_text(json.dumps(_V13_GREEN_HOUSE))
    (directory / "warm_room.json").write_text(json.dumps(_V13_WARM_ROOM))


def test_migrate_skips_existing_unless_forced(tmp_path: Path):
    store = str(tmp_path / "store")
    legacy = tmp_path / "legacy"
    _write_v13_fixtures(legacy)

    first = _invoke("migrate", "--from", str(legacy), "--store", store)
    assert first["migrated"] == 2
    assert first["skipped"] == 0
    assert first["failed"] == 0
    assert first["cost_usd"] == 0
    assert first["by_version"] == {"1.3": 2}

    again = _invoke("migrate", "--from", str(legacy), "--store", store)
    assert again["migrated"] == 0
    assert again["skipped"] == 2

    forced = _invoke("migrate", "--from", str(legacy), "--store", store, "--force")
    assert forced["migrated"] == 2
    assert forced["skipped"] == 0


def test_migrate_reports_failures(tmp_path: Path):
    store = str(tmp_path / "store")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "bad.json").write_text("{not valid json")

    summary = _invoke("migrate", "--from", str(legacy), "--store", store)
    assert summary["migrated"] == 0
    assert summary["failed"] == 1
    assert len(summary["failures"]) == 1
    assert "bad.json" in summary["failures"][0]


def test_migrate_retrofit_resolve_workflow(tmp_path: Path):
    store = str(tmp_path / "store")
    legacy = tmp_path / "legacy"
    _write_v13_fixtures(legacy)

    migrated = _invoke("migrate", "--from", str(legacy), "--store", store)
    assert migrated["migrated"] == 2

    retrofit_summary = _invoke("retrofit", "--only", "all", "--store", store)
    passes = retrofit_summary["passes"]
    assert passes["classify_kind"]["entries_scanned"] == 2
    assert passes["tag_domain"]["entries_scanned"] == 2
    assert passes["spans"]["entries_scanned"] == 2
    # Both headwords are two words (ambiguous by rule), and neither sense had a
    # taxonomy-mappable domain, so both passes made a real model call.
    assert retrofit_summary["cost_usd"] > 0

    first_resolve = _invoke("resolve", "--all", "--store", store)
    assert first_resolve["resolved"] >= 1
    assert first_resolve["cost_usd"] > 0
    assert first_resolve["mean_confidence"] == pytest.approx(0.9)

    second_resolve = _invoke("resolve", "--all", "--store", store)
    assert second_resolve["resolved"] == 0
    assert second_resolve["cost_usd"] == 0


def test_resolve_single_headword(tmp_path: Path):
    store = str(tmp_path / "store")
    _invoke("generate", "--headword", "abseil", "--store", store)
    # Every scripted sense carries a hypernym relation to "broader_thing"; generate it so
    # it is in the store for the resolver to choose a sense from.
    _invoke("generate", "--headword", "broader_thing", "--store", store)

    summary = _invoke("resolve", "--headword", "abseil", "--store", store)
    assert summary["resolved"] >= 1
    assert summary["cost_usd"] > 0
    assert summary["mean_confidence"] == pytest.approx(0.9)


def test_resolve_requires_exactly_one_of_headword_or_all(tmp_path: Path):
    result = runner.invoke(cli.app, ["resolve", "--store", str(tmp_path / "store")])
    assert result.exit_code != 0


def test_enrich_requires_exactly_one_selector(tmp_path: Path):
    store = str(tmp_path / "store")
    none_given = runner.invoke(cli.app, ["enrich", "--store", store, "--reading-levels", "grade_1"])
    assert none_given.exit_code != 0

    both_given = runner.invoke(
        cli.app,
        [
            "enrich",
            "--headword",
            "abseil",
            "--all",
            "--store",
            store,
            "--reading-levels",
            "grade_1",
        ],
    )
    assert both_given.exit_code != 0


def test_enrich_from_list_only_touches_listed_words(tmp_path: Path):
    store = str(tmp_path / "store")
    for word in ("abseil", "bramble", "candle"):
        _invoke("generate", "--headword", word, "--store", store)

    word_list = tmp_path / "words.tsv"
    word_list.write_text("word\tpos\nabseil\tverb\ncandle\tnoun\n")

    summary = _invoke(
        "enrich",
        "--from-list",
        str(word_list),
        "--reading-levels",
        "grade_1",
        "--store",
        store,
    )
    assert summary["entries_scanned"] == 2
    assert summary["entries_changed"] == 2
    assert summary["entries_skipped"] == 0
    assert summary["entries_failed"] == 0
    assert summary["renditions_added"] == 6  # 3 senses x 1 level, on each of the 2 listed words
    assert summary["cost_usd"] > 0
    assert len(summary["failures"]) == 0

    # "bramble" was not on the list, so it kept only its canonical gloss.
    shown = _invoke("show", "--headword", "bramble", "--store", store)
    assert len(shown["pos_entries"][0]["senses"][0]["gloss"]) == 1

    second = _invoke(
        "enrich",
        "--from-list",
        str(word_list),
        "--reading-levels",
        "grade_1",
        "--store",
        store,
    )
    assert second["entries_changed"] == 0
    assert second["entries_skipped"] == 2
    assert second["cost_usd"] == 0


def test_enrich_all_respects_limit(tmp_path: Path):
    store = str(tmp_path / "store")
    for word in ("abseil", "bramble", "candle"):
        _invoke("generate", "--headword", word, "--store", store)

    summary = _invoke(
        "enrich", "--all", "--limit", "1", "--reading-levels", "grade_1", "--store", store
    )
    assert summary["entries_scanned"] == 1
    assert summary["entries_changed"] == 1
    assert summary["renditions_added"] == 3


def test_enrich_all_offset_skips_leading_entries(tmp_path: Path):
    store = str(tmp_path / "store")
    for word in ("abseil", "bramble", "candle"):
        _invoke("generate", "--headword", word, "--store", store)

    # Sorted store order is abseil, bramble, candle; offset 1 skips abseil.
    summary = _invoke(
        "enrich",
        "--all",
        "--offset",
        "1",
        "--limit",
        "1",
        "--reading-levels",
        "grade_1",
        "--store",
        store,
    )
    assert summary["entries_scanned"] == 1
    shown = _invoke("show", "--headword", "bramble", "--store", store)
    assert len(shown["pos_entries"][0]["senses"][0]["gloss"]) == 2
    shown_abseil = _invoke("show", "--headword", "abseil", "--store", store)
    assert len(shown_abseil["pos_entries"][0]["senses"][0]["gloss"]) == 1


def test_enrich_all_dry_run_estimates_without_calling(tmp_path: Path):
    store = str(tmp_path / "store")
    for word in ("abseil", "bramble"):
        _invoke("generate", "--headword", word, "--store", store)

    summary = _invoke(
        "enrich", "--all", "--reading-levels", "grade_1", "--store", store, "--dry-run"
    )
    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0
    assert summary["entries_scanned"] == 2
    assert summary["entries_would_change"] == 2
    assert summary["estimated_calls"] == 6  # 3 senses x 2 entries x 1 requested field
    assert summary["estimated_cost_usd"] > 0

    # No entry actually changed: the dry run made no calls.
    shown = _invoke("show", "--headword", "abseil", "--store", store)
    assert len(shown["pos_entries"][0]["senses"][0]["gloss"]) == 1


def test_enrich_all_budget_stop_ends_sweep_cleanly(tmp_path: Path):
    store = str(tmp_path / "store")
    for word in ("abseil", "bramble", "candle"):
        _invoke("generate", "--headword", word, "--store", store)

    result = runner.invoke(
        cli.app,
        [
            "enrich",
            "--all",
            "--reading-levels",
            "grade_1",
            "--store",
            store,
            "--budget",
            "0.0001",
            "--concurrency",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["stop_reason"] == "budget"
    assert summary["entries_changed"] == 0
