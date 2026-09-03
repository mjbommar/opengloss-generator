"""Example 8: judge finished entries with a second model and record what it found.

The whole suite is offline: ``scripted_model`` answers the judge's contract with a
verdict carrying exactly one defect of each shape, so every mapping from a verdict field
to an :class:`~opengloss_generator.schema.Assessment` is asserted from one call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import StoreConfig
from opengloss_generator.contracts import (
    QA_MAX_SENSES,
    DraftQAVerdict,
    DraftRenditionVerdict,
    DraftSenseVerdict,
)
from opengloss_generator.prompts import QA_INSTRUCTIONS, QA_TEXT_WORD_LIMIT, build_qa_prompt
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    Assessment,
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    QAFlag,
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
from opengloss_generator.workflows.qa import (
    RENDITION_DIMENSIONS,
    SENSE_DIMENSIONS,
    QAOutcome,
    _apply_verdict,
    _sample_entry,
    judge_entry,
    run_qa,
    stratified_sample,
)
from tests.conftest import (
    QA_CLEAN_ENTRY_SCORE,
    QA_CLEAN_HEADWORD,
    QA_ENCYCLOPEDIA_ISSUE,
    QA_ENTRY_SCORE,
    QA_GLOSS_ISSUE,
    QA_INVALID_RELATION,
    QA_NOTES,
    QA_RENDITION_ISSUE,
    _qa_verdict_payload,
)

runner = CliRunner()


def _qa_entry(headword: str = "abseil", *, senses: int = 2) -> Lexeme:
    """Build an entry carrying every rendition the judge's sample asks for."""
    built = []
    for index in range(senses):
        gloss = Renditions[str](
            root=[
                canonical_rendition(f"Definition number {index} of the headword under test."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content=f"An easy way to say it, number {index}.",
                ),
                Rendition[str](
                    reading_level=ReadingLevel.COLLEGE,
                    style=Register.PLAIN,
                    content=f"A collegiate way to say it, number {index}.",
                ),
                Rendition[str](
                    reading_level=ReadingLevel.NEUTRAL,
                    style=Register.TECHNICAL,
                    content=f"A technical way to say it, number {index}.",
                ),
            ]
        )
        examples = Renditions[Example](
            root=[
                canonical_rendition(Example(text=f"The {headword} appeared, number {index}.")),
                Rendition[Example](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content=Example(text=f"The {headword} is here, number {index}."),
                ),
            ]
        )
        built.append(
            Sense(
                index=index,
                gloss=gloss,
                examples=examples,
                relations=[
                    Relation(type=RelationType.SYNONYM, target=RelationTarget(term="rappel")),
                    Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="rope")),
                ],
                domain=DomainTag.NATURE_GENERAL,
            )
        )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=built)],
        encyclopedia=Renditions[str](
            root=[
                canonical_rendition("Encyclopedic prose about the headword. " * 6),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content="An easy encyclopedia opening about the thing.",
                ),
                Rendition[str](
                    reading_level=ReadingLevel.COLLEGE,
                    style=Register.PLAIN,
                    content="A collegiate encyclopedia opening about the thing.",
                ),
            ]
        ),
    )


# --------------------------------------------------------------------------------------
# The contract and the prompt
# --------------------------------------------------------------------------------------


def test_contract_validates_the_scripted_payload():
    entry = _qa_entry(senses=2)
    prompt = build_qa_prompt(
        entry.headword,
        entry.kind.value,
        [("verb 0", "A gloss.", ["An example."], ["synonym->rappel"], "nature.general")],
        [("verb 0 gloss grade_1/plain", "Easy text."), ("encyclopedia grade_1/plain", "Prose.")],
        "Canonical prose.",
    )
    verdict = DraftQAVerdict.model_validate(_qa_verdict_payload(prompt))
    assert verdict.entry_score == QA_ENTRY_SCORE
    assert [v.sense_ref for v in verdict.sense_verdicts] == [1]
    assert [v.rendition_ref for v in verdict.rendition_verdicts] == [1, 2]
    assert verdict.flags == [QAFlag.FACTUAL_ERROR]


def test_every_flag_value_is_documented_in_the_instructions():
    """A closed vocabulary the judge is not shown is a vocabulary it cannot use."""
    for flag in QAFlag:
        assert flag.value in QA_INSTRUCTIONS, flag


def test_instructions_are_long_enough_to_be_cacheable():
    """Well over the 1,024-token prefix a provider cache needs (docs/CORE-DIARY.md)."""
    assert len(QA_INSTRUCTIONS) // 4 > 1100


def test_prompt_truncates_long_text_and_caps_the_sense_list():
    long_text = " ".join(f"word{i}" for i in range(400))
    senses = [
        (f"noun {i}", long_text, [long_text], ["synonym->thing"], "nature.general")
        for i in range(QA_MAX_SENSES + 4)
    ]
    prompt = build_qa_prompt("thing", "simplex", senses, [("enc", long_text)], long_text)

    assert f"Senses ({QA_MAX_SENSES}):" in prompt
    assert f"  {QA_MAX_SENSES}. " in prompt
    assert f"  {QA_MAX_SENSES + 1}. " not in prompt
    assert "word399" not in prompt
    assert f"word{QA_TEXT_WORD_LIMIT - 1} ..." in prompt


def test_prompt_shows_the_entry_compactly():
    entry = _qa_entry()
    prompt = build_qa_prompt(
        entry.headword,
        entry.kind.value,
        [("verb 0", "A gloss.", ["An example."], ["synonym->rappel", "hypernym->rope"], "nature")],
        [("verb 0 gloss grade_1/plain", "Easy text.")],
        None,
    )
    assert "Headword: abseil" in prompt
    assert "Kind: simplex" in prompt
    assert "relations: synonym->rappel, hypernym->rope" in prompt
    assert "Encyclopedia" not in prompt


# --------------------------------------------------------------------------------------
# Verdict -> Assessment
# --------------------------------------------------------------------------------------


async def test_verdict_is_written_onto_entry_senses_and_renditions(session):
    entry = _qa_entry(senses=2)
    outcome = await judge_entry(entry, session.stages)

    assert outcome.entries_judged == 1
    assert outcome.calls == 1
    assert outcome.cost_usd > 0

    # Entry level: the score, the judge's identity, and the union of its flags.
    assert entry.assessment is not None
    assert entry.assessment.qa_score == float(QA_ENTRY_SCORE)
    assert entry.assessment.judge_model == "claude-opus-5"
    assert entry.assessment.judged_at is not None
    assert entry.assessment.qa_flags == [QAFlag.FACTUAL_ERROR]

    # Sense level: the first sense failed two of six dimensions, the second failed none.
    first, second = entry.pos_entries[0].senses
    assert first.assessment is not None
    assert first.assessment.qa_score == pytest.approx(100 * 4 / 6, abs=0.1)
    assert set(first.assessment.qa_flags) == {QAFlag.FACTUAL_ERROR, QAFlag.TERMINOLOGY_ERROR}
    assert second.assessment is not None
    assert second.assessment.qa_score == 100.0
    assert second.assessment.qa_flags == []

    # Rendition level: only the first sampled rendition (sense 0's grade_1 gloss) failed.
    flagged = first.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert flagged is not None
    assert flagged.assessment is not None
    assert flagged.assessment.qa_flags == [QAFlag.AUDIENCE_INAPPROPRIATE]
    clean = first.gloss.get(ReadingLevel.COLLEGE, Register.PLAIN)
    assert clean is not None
    assert clean.assessment is None

    assert outcome.senses_judged == 2
    assert outcome.sense_defects == {"gloss_accurate": 1, "relations_valid": 1}
    assert outcome.renditions_judged["gloss grade_1/plain"] == 2
    assert outcome.rendition_defects == {"gloss grade_1/plain": 1}
    assert outcome.encyclopedia_defects == 1


async def test_free_text_issues_survive_on_a_zero_cost_provenance_note(session):
    entry = _qa_entry(senses=1)
    await judge_entry(entry, session.stages)

    qa_records = [p for p in entry.provenance.values() if p.stage is StageName.QA]
    priced = [p for p in qa_records if p.cost_usd > 0]
    notes = [p for p in qa_records if p.note]
    assert len(priced) == 1
    assert len(notes) == 1
    assert notes[0].cost_usd == 0.0
    assert notes[0].output_tokens == 0
    for fragment in (QA_GLOSS_ISSUE, QA_INVALID_RELATION, QA_RENDITION_ISSUE, QA_NOTES):
        assert fragment in notes[0].note
    assert QA_ENCYCLOPEDIA_ISSUE in notes[0].note


async def test_a_clean_verdict_leaves_no_flags_anywhere(session):
    entry = _qa_entry(QA_CLEAN_HEADWORD, senses=1)
    outcome = await judge_entry(entry, session.stages)

    assert entry.assessment is not None
    assert entry.assessment.qa_score == float(QA_CLEAN_ENTRY_SCORE)
    assert entry.assessment.qa_flags == []
    sense = entry.pos_entries[0].senses[0]
    assert sense.assessment is not None
    assert sense.assessment.qa_score == 100.0
    assert all(r.assessment is None for r in sense.gloss)
    assert outcome.sense_defects == {}
    assert outcome.rendition_defects == {}
    assert not outcome.issues


async def test_judging_never_writes_the_flags_the_deterministic_passes_own(session):
    """`og.readability_miss` and `og.headword_absent` drive priced rewrite passes."""
    entry = _qa_entry(senses=2)
    await judge_entry(entry, session.stages)

    owned = {QAFlag.OG_READABILITY_MISS, QAFlag.OG_HEADWORD_ABSENT}
    for _, sense, _ in entry.iter_senses():
        for rendition in [*sense.gloss, *sense.examples]:
            if rendition.assessment is not None:
                assert not owned & set(rendition.assessment.qa_flags)


async def test_a_retired_sense_is_never_judged(session):
    entry = _qa_entry(senses=2)
    entry.pos_entries[0].senses[1].retired = True
    outcome = await judge_entry(entry, session.stages)

    assert outcome.senses_judged == 1
    assert entry.pos_entries[0].senses[1].assessment is None


async def test_an_entry_with_no_live_senses_costs_nothing(session):
    entry = _qa_entry(senses=1)
    entry.pos_entries[0].senses[0].retired = True
    outcome = await judge_entry(entry, session.stages)

    assert outcome.cost_usd == 0.0
    assert outcome.calls == 0
    assert outcome.entries_skipped == 1
    assert entry.assessment is None


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------


async def test_run_qa_is_idempotent_unless_forced(session):
    store = session.store
    store.write(_qa_entry("abseil"))
    ids = ["abseil"]

    first = await run_qa(store, session.stages, lexeme_ids=ids)
    assert first.entries_judged == 1
    assert first.entries_changed == ["abseil"]

    second = await run_qa(store, session.stages, lexeme_ids=ids)
    assert second.entries_judged == 0
    assert second.entries_skipped == 1
    assert second.cost_usd == 0.0

    forced = await run_qa(store, session.stages, lexeme_ids=ids, force=True)
    assert forced.entries_judged == 1
    assert forced.cost_usd > 0


async def test_run_qa_skips_ids_with_no_entry(session):
    outcome = await run_qa(session.store, session.stages, lexeme_ids=["absent"])
    assert outcome.entries_judged == 0
    assert outcome.entries_skipped == 1


async def test_run_qa_merges_across_entries(session):
    store = session.store
    store.write(_qa_entry("abseil"))
    store.write(_qa_entry(QA_CLEAN_HEADWORD))

    outcome = await run_qa(store, session.stages, lexeme_ids=["abseil", QA_CLEAN_HEADWORD])

    assert outcome.entries_judged == 2
    assert sorted(outcome.scores) == [float(QA_ENTRY_SCORE), float(QA_CLEAN_ENTRY_SCORE)]
    assert outcome.buckets() == {"<60": 0, "60-79": 1, "80-89": 0, "90+": 1}
    assert outcome.flags == {"factual_error": 1}
    assert outcome.entries_changed == sorted(["abseil", QA_CLEAN_HEADWORD])
    reread = store.read("abseil")
    assert reread is not None
    assert reread.assessment is not None
    assert reread.assessment.judge_model == "claude-opus-5"


# --------------------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------------------


def _sample_store(tmp_path: Path) -> tuple[LexemeStore, list[str]]:
    """Build a store spanning three sense buckets across three frequency terciles."""
    store = LexemeStore(StoreConfig(root=tmp_path / "sample", fsync_on_write=False))
    words: list[str] = []
    for tercile in range(3):
        for slot, senses in enumerate((1, 3, 5)):
            for copy in range(2):
                word = f"w{tercile}{slot}{copy}"
                words.append(word)
                store.write(_qa_entry(word, senses=senses))
    return store, words


def test_stratified_sample_is_deterministic(tmp_path: Path):
    store, words = _sample_store(tmp_path)
    first = stratified_sample(store, words, 9, seed=7)
    again = stratified_sample(store, words, 9, seed=7)
    assert first == again == sorted(first)

    other = stratified_sample(store, words, 9, seed=8)
    assert len(other) == 9
    assert set(other) != set(first)


def test_stratified_sample_covers_every_stratum(tmp_path: Path):
    store, words = _sample_store(tmp_path)
    chosen = stratified_sample(store, words, 9, seed=1)

    strata = set()
    for lexeme_id in chosen:
        entry = store.read(lexeme_id)
        assert entry is not None
        bucket = {1: "1", 3: "2-3", 5: "4+"}[entry.sense_count()]
        strata.add((lexeme_id[1], bucket))
    # Three terciles x three sense buckets, one entry drawn from each.
    assert len(strata) == 9


def test_stratified_sample_skips_words_with_no_entry(tmp_path: Path):
    store, words = _sample_store(tmp_path)
    chosen = stratified_sample(store, [*words, "absent_word"], 30, seed=3)
    assert "absent-word" not in chosen
    assert len(chosen) == len(words)


def test_stratified_sample_returns_the_whole_population_when_asked_for_more(tmp_path: Path):
    store, words = _sample_store(tmp_path)
    assert stratified_sample(store, words, 500, seed=2) == sorted(words)


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------


def test_metrics_aggregate_a_hand_built_outcome():
    outcome = QAOutcome(
        entries_judged=4,
        calls=4,
        cost_usd=0.08,
        scores=[42.0, 65.0, 85.0, 96.0],
        senses_judged=10,
        sense_defects={"gloss_accurate": 2, "domain_fits": 5},
        renditions_judged={"gloss grade_1/plain": 8, "encyclopedia college/plain": 4},
        rendition_defects={"gloss grade_1/plain": 2},
        encyclopedia_judged=4,
        encyclopedia_defects=1,
        flags={"factual_error": 3, "awkward_style": 1},
        issues=[f"issue {i}" for i in range(30)],
    )
    report = outcome.as_dict()

    assert report["mean_score"] == 72.0
    assert report["score_buckets"] == {"<60": 1, "60-79": 1, "80-89": 1, "90+": 1}
    assert report["cost_usd_per_entry"] == 0.02
    rates = report["sense_defect_rates"]
    assert set(rates) == set(SENSE_DIMENSIONS)
    assert rates["gloss_accurate"] == 0.2
    assert rates["domain_fits"] == 0.5
    assert rates["examples_natural"] == 0.0
    assert report["rendition_defect_rates"] == {
        "encyclopedia college/plain": 0.0,
        "gloss grade_1/plain": 0.25,
    }
    assert report["flag_histogram"] == {"awkward_style": 1, "factual_error": 3}
    assert len(report["top_issues"]) == 20


def test_an_empty_outcome_reports_no_mean_and_zero_rates():
    report = QAOutcome().as_dict()
    assert report["mean_score"] is None
    assert report["cost_usd_per_entry"] is None
    assert report["sense_defect_rates"] == dict.fromkeys(SENSE_DIMENSIONS, 0.0)
    assert report["rendition_defect_rates"] == {}


def test_the_dimension_lists_match_the_contract():
    """A dimension added to the contract but not to the lists would inflate every score."""
    assert set(SENSE_DIMENSIONS) <= set(DraftSenseVerdict.model_fields)
    assert set(RENDITION_DIMENSIONS) <= set(DraftRenditionVerdict.model_fields)
    booleans = {
        name for name, info in DraftSenseVerdict.model_fields.items() if info.annotation is bool
    }
    assert booleans == set(SENSE_DIMENSIONS)
    assert {
        name for name, info in DraftRenditionVerdict.model_fields.items() if info.annotation is bool
    } == set(RENDITION_DIMENSIONS)


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


def test_cli_dry_run_prints_the_sample_and_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    store, words = _sample_store(tmp_path)
    word_list = tmp_path / "core.tsv"
    word_list.write_text("word\n" + "\n".join(words) + "\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "qa",
            "--from-list",
            str(word_list),
            "--store",
            str(store.root),
            "--sample",
            "6",
            "--seed",
            "5",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["sample_size"] == 6
    assert len(summary["sample"]) == 6
    assert summary["seed"] == 5
    estimate = summary["qa"]
    assert estimate["judge_model"] == "claude-opus-5"
    assert estimate["estimated_calls"] == 6
    assert estimate["estimated_cost_usd"] > 0
    assert estimate["estimated_cost_usd"] == pytest.approx(
        estimate["estimated_cost_usd_per_entry"] * 6, rel=1e-6
    )


def test_cli_rejects_a_sample_below_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    word_list = tmp_path / "core.txt"
    word_list.write_text("abseil\n", encoding="utf-8")
    result = runner.invoke(
        cli.app, ["qa", "--from-list", str(word_list), "--sample", "0", "--dry-run"]
    )
    assert result.exit_code != 0


def test_cli_writes_the_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted_model
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    store, words = _sample_store(tmp_path)
    word_list = tmp_path / "core.tsv"
    word_list.write_text("word\n" + "\n".join(words) + "\n", encoding="utf-8")
    report = tmp_path / "reports" / "qa.json"

    original = RunSession.__init__

    def patched(
        self, config, *, model_override=None, run_id=None, install_signal_handler=False
    ) -> None:
        original(self, config, model_override=scripted_model, run_id=run_id)

    monkeypatch.setattr(RunSession, "__init__", patched)

    result = runner.invoke(
        cli.app,
        [
            "qa",
            "--from-list",
            str(word_list),
            "--store",
            str(store.root),
            "--sample",
            "3",
            "--seed",
            "4",
            "--report",
            str(report),
        ],
    )
    assert result.exit_code == 0, result.output
    metrics = json.loads(report.read_text(encoding="utf-8"))
    assert metrics["entries_judged"] == 3
    assert metrics["mean_score"] == float(QA_ENTRY_SCORE)
    assert metrics["cost_usd"] > 0
    assert json.loads(result.stdout)["qa"] == metrics


def test_an_existing_assessment_is_updated_not_replaced():
    """The judge must not drop a readability grade another pass measured."""
    entry = _qa_entry(senses=1)
    entry.assessment = Assessment(readability_grade=9.1, human_verified=True)
    entry.assessment.flag(QAFlag.OG_HEADWORD_INITIAL)

    senses, sampled = _sample_entry(entry)
    prompt = build_qa_prompt(entry.headword, entry.kind.value, [], [], None)
    verdict = DraftQAVerdict.model_validate(_qa_verdict_payload(prompt))
    _apply_verdict(
        entry, verdict, senses, sampled, judge_model="claude-opus-5", outcome=QAOutcome()
    )

    assert entry.assessment.readability_grade == 9.1
    assert entry.assessment.human_verified is True
    assert QAFlag.OG_HEADWORD_INITIAL in entry.assessment.qa_flags
    assert QAFlag.FACTUAL_ERROR in entry.assessment.qa_flags
