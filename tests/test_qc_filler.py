"""``qc filler`` (F8): the corpus-level n-gram / sentence-opener filler detector.

Every test builds its own store under ``tmp_path``. Nothing here calls a model —
:func:`~opengloss_generator.qc.filler.analyze_filler` makes none at all — so the
assertions are about corpus counts, per-entry scores, and stored ``OG_FILLER`` flags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.qc.filler import (
    FillerConfig,
    _RenditionRef,
    _score_entries,
    analyze_filler,
    apply_filler_flags,
    calibrate_thresholds,
    phrases_in,
)
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    QAFlag,
    ReadingLevel,
    Register,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore

WORKERS = 4


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _entry(headword: str, example_text: str) -> Lexeme:
    """Build a one-sense noun entry whose canonical example is ``example_text``."""
    sense = Sense.of(0, f"A definition of {headword}, written for the filler tests.")
    sense.examples.add(canonical_rendition(Example(text=example_text)))
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )


def _write(store: LexemeStore, *entries: Lexeme) -> None:
    """Persist every entry."""
    for entry in entries:
        store.write(entry)


# --------------------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------------------


# Six independently-generated entries, each opening its one canonical example the same
# stilted way (D-49's measured shape: "researchers"/"the study" tells) — the corpus-level
# signal this module exists to catch even where no per-example regex would fire on any
# one of them alone.
_FILLER_HEADWORDS_AND_TAILS = [
    ("abacus", "a counting device used since antiquity"),
    ("bellows", "a tool for pumping air into a fire"),
    ("compass", "an instrument for finding direction"),
    ("dovetail", "a joint shaped like a bird's tail"),
    ("estuary", "a place where a river meets the sea"),
    ("firkin", "a small cask for butter or ale"),
]


def _filler_entries() -> list[Lexeme]:
    return [
        _entry(word, f"The researchers found that {tail}.")
        for word, tail in _FILLER_HEADWORDS_AND_TAILS
    ]


# Ten entries whose example sentences share no repeated 4-gram and no repeated 2- or
# 3-word opener with one another — a corpus this module should leave alone entirely.
_VARIED_EXAMPLES = [
    ("otter", "A playful otter floated on its back."),
    ("lantern", "Warm light spilled from the old lantern."),
    ("canyon", "Wind carved the canyon over many centuries."),
    ("violin", "She tuned her violin before the concert."),
    ("harbor", "Fishing boats crowded the small harbor."),
    ("meadow", "Wildflowers covered the meadow in spring."),
    ("sundial", "He checked the time on a sundial."),
    ("granite", "Builders quarried granite from the hillside."),
    ("thicket", "A fox slipped through the dense thicket."),
    ("orchard", "Apples ripened slowly in the orchard."),
]


def _varied_entries() -> list[Lexeme]:
    return [_entry(word, text) for word, text in _VARIED_EXAMPLES]


async def test_an_obvious_filler_corpus_is_caught(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_filler_entries(), *_varied_entries())

    report = analyze_filler(store)

    ngram_phrases = {" ".join(f.key) for f in report.ngram_findings}
    assert "the researchers found that" in ngram_phrases

    opener_phrases = {" ".join(f.key) for f in report.opener_findings.get(2, [])}
    assert "the researchers" in opener_phrases

    # Exactly the six stilted renditions are offenders, none of the varied ones.
    offending_headwords = {ref.lexeme_id for ref in report.offending_refs}
    assert offending_headwords == {word for word, _ in _FILLER_HEADWORDS_AND_TAILS}

    finding = next(
        f for f in report.ngram_findings if f.key == ("the", "researchers", "found", "that")
    )
    assert finding.count == len(_FILLER_HEADWORDS_AND_TAILS)
    assert 1 <= len(finding.example_report_ids) <= report.config.max_examples


async def test_a_varied_corpus_is_not_flagged(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_varied_entries())

    report = analyze_filler(store)

    assert report.ngram_findings == []
    assert all(findings == [] for findings in report.opener_findings.values())
    assert report.offending_refs == []
    assert report.entries_scanned == len(_VARIED_EXAMPLES)


async def test_retired_senses_are_excluded_from_the_scan(tmp_path: Path):
    # A retired sense's example must not feed the corpus counts at all — "only live
    # senses" (F8).
    entries = _filler_entries()
    for entry in entries:
        entry.pos_entries[0].senses[0].retired = True
    store = _store(tmp_path)
    _write(store, *entries, *_varied_entries())

    report = analyze_filler(store)

    assert report.ngram_findings == []
    assert report.offending_refs == []
    assert report.senses_live == len(_VARIED_EXAMPLES)


# --------------------------------------------------------------------------------------
# Per-entry diagnostic scores
# --------------------------------------------------------------------------------------


async def test_entry_scores_reward_length_and_variety():
    def ref(lexeme_id: str, text: str) -> _RenditionRef:
        return _RenditionRef(
            lexeme_id=lexeme_id,
            report_id=f"{lexeme_id}#ref",
            kind="example",
            sense_id=None,
            level=ReadingLevel.NEUTRAL,
            style=Register.PLAIN,
            text=text,
        )

    config = FillerConfig()
    refs = [
        ref("short", "No no no."),
        ref(
            "ideal",
            "The old lighthouse keeper climbed the spiral stairs every evening to light "
            "the lamp before the fishing boats returned home through the fog.",
        ),
    ]
    scores = _score_entries(refs, config)

    assert scores["short"].information_density < scores["ideal"].information_density
    assert scores["short"].uniqueness <= 1.0
    assert 0.0 <= scores["ideal"].uniqueness <= 1.0


# --------------------------------------------------------------------------------------
# --flag / --unflag
# --------------------------------------------------------------------------------------


async def test_flagging_is_applied_and_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_filler_entries(), *_varied_entries())
    report = analyze_filler(store)
    offending = {word for word, _ in _FILLER_HEADWORDS_AND_TAILS}
    assert {ref.lexeme_id for ref in report.offending_refs} == offending

    outcome = await apply_filler_flags(store, report, workers=WORKERS)
    assert outcome.renditions_flagged == len(offending)
    assert outcome.entries_changed == len(offending)

    for word in offending:
        entry = store.read(word)
        assert entry is not None
        rendition = entry.pos_entries[0].senses[0].examples[0]
        assert rendition.assessment is not None
        assert rendition.assessment.qa_flags == [QAFlag.OG_FILLER]

    for word, _ in _VARIED_EXAMPLES:
        entry = store.read(word)
        assert entry is not None
        rendition = entry.pos_entries[0].senses[0].examples[0]
        assert rendition.assessment is None or QAFlag.OG_FILLER not in rendition.assessment.qa_flags

    # Re-running the whole detect-then-flag cycle must not duplicate the flag.
    report_again = analyze_filler(store)
    outcome_again = await apply_filler_flags(store, report_again, workers=WORKERS)
    assert outcome_again.renditions_flagged == 0
    assert outcome_again.renditions_already == len(offending)
    assert outcome_again.entries_changed == 0

    for word in offending:
        entry = store.read(word)
        assert entry is not None
        rendition = entry.pos_entries[0].senses[0].examples[0]
        assert rendition.assessment is not None
        assert rendition.assessment.qa_flags == [QAFlag.OG_FILLER]


async def test_unflag_reverses_flagging_and_is_itself_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_filler_entries(), *_varied_entries())
    report = analyze_filler(store)
    offending = {word for word, _ in _FILLER_HEADWORDS_AND_TAILS}

    await apply_filler_flags(store, report, workers=WORKERS)

    reverse_outcome = await apply_filler_flags(store, report, workers=WORKERS, remove=True)
    assert reverse_outcome.renditions_unflagged == len(offending)
    assert reverse_outcome.entries_changed == len(offending)

    for word in offending:
        entry = store.read(word)
        assert entry is not None
        rendition = entry.pos_entries[0].senses[0].examples[0]
        assert rendition.assessment is None or QAFlag.OG_FILLER not in rendition.assessment.qa_flags

    # Unflagging an already-clean store is a no-op, not an error.
    reverse_again = await apply_filler_flags(store, report, workers=WORKERS, remove=True)
    assert reverse_again.renditions_unflagged == 0
    assert reverse_again.renditions_already == len(offending)
    assert reverse_again.entries_changed == 0


# --------------------------------------------------------------------------------------
# --fields (D-66)
# --------------------------------------------------------------------------------------


#: One unrelated, mutually distinct example sentence per filler headword — no shared
#: 4-gram or opener among them, mirroring ``_VARIED_EXAMPLES``' own shape but kept
#: separate from it so no text is ever written twice under two different headwords.
_UNRELATED_EXAMPLES = [
    "Someone painted the old fence a bright shade of blue.",
    "The children built a sandcastle near the tide line.",
    "A gentle breeze moved through the open window.",
    "The baker pulled a fresh loaf from the oven.",
    "Two cats napped together on the warm windowsill.",
    "The hikers paused to admire the distant peaks.",
]


def _entries_with_filler_encyclopedia_only() -> list[Lexeme]:
    """Six entries carrying the filler tell in their encyclopedia text, not their example.

    Each example is its own distinct, unrepeated sentence, so it does not itself become a
    corpus-level finding — the point of this fixture is that "examples" and "encyclopedia"
    scopes must disagree about which entries offend.
    """
    entries = []
    for (word, tail), unrelated_text in zip(
        _FILLER_HEADWORDS_AND_TAILS, _UNRELATED_EXAMPLES, strict=True
    ):
        entry = _entry(word, unrelated_text)
        entry.encyclopedia.add(canonical_rendition(f"The researchers found that {tail}."))
        entries.append(entry)
    return entries


async def test_fields_restricts_the_scan_to_one_kind_of_rendition(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_entries_with_filler_encyclopedia_only(), *_varied_entries())

    examples_report = analyze_filler(store, fields="examples")
    encyclopedia_report = analyze_filler(store, fields="encyclopedia")

    assert examples_report.units_scanned == len(_FILLER_HEADWORDS_AND_TAILS) + len(_VARIED_EXAMPLES)
    assert examples_report.offending_refs == []

    assert encyclopedia_report.units_scanned == len(_FILLER_HEADWORDS_AND_TAILS)
    assert {ref.lexeme_id for ref in encyclopedia_report.offending_refs} == {
        word for word, _ in _FILLER_HEADWORDS_AND_TAILS
    }


async def test_fields_all_is_the_union_of_examples_and_encyclopedia(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_entries_with_filler_encyclopedia_only(), *_varied_entries())

    all_report = analyze_filler(store, fields="all")

    # Each of the six filler entries contributes both an example and an encyclopedia
    # rendition; the ten varied entries contribute only an example.
    assert all_report.units_scanned == 2 * len(_FILLER_HEADWORDS_AND_TAILS) + len(_VARIED_EXAMPLES)
    assert {ref.lexeme_id for ref in all_report.offending_refs} == {
        word for word, _ in _FILLER_HEADWORDS_AND_TAILS
    }


def test_fields_rejects_an_unknown_value(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="fields"):
        analyze_filler(store, fields="bogus")


# --------------------------------------------------------------------------------------
# phrases_in
# --------------------------------------------------------------------------------------


async def test_phrases_in_names_the_matching_finding(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_filler_entries(), *_varied_entries())
    report = analyze_filler(store)

    matches = phrases_in(
        "The researchers found that a counting device used since antiquity.", report
    )
    assert "the researchers found that" in matches

    assert phrases_in("Nothing here matches any of the findings at all.", report) == []


# --------------------------------------------------------------------------------------
# calibrate_thresholds (D-66)
# --------------------------------------------------------------------------------------


async def test_calibrate_thresholds_measures_flag_rate_at_each_point(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, *_filler_entries(), *_varied_entries())
    total_units = len(_FILLER_HEADWORDS_AND_TAILS) + len(_VARIED_EXAMPLES)

    points = calibrate_thresholds(
        store,
        thresholds=[(0.5, 0.5, 100), (0.00025, 0.0025, 5)],
        fields="examples",
    )

    assert len(points) == 2
    # An impossibly high bar (and floor) flags nothing.
    assert points[0].renditions_flagged == 0
    assert points[0].flag_rate == 0.0
    assert points[0].units_scanned == total_units

    # The calibrated default catches exactly the six stilted renditions.
    assert points[1].renditions_flagged == len(_FILLER_HEADWORDS_AND_TAILS)
    assert points[1].units_scanned == total_units
    assert points[1].flag_rate == pytest.approx(len(_FILLER_HEADWORDS_AND_TAILS) / total_units)
    assert any(row["phrase"] == "the researchers found that" for row in points[1].top_phrases)


def test_calibrate_thresholds_rejects_an_unknown_fields_value(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="fields"):
        calibrate_thresholds(store, thresholds=[(0.1, 0.1, 5)], fields="bogus")
