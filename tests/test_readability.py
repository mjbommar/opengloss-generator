"""The deterministic readability measurement and the markdown strip.

These are pure functions over text, so the tests are exact: a hand-computed
Flesch-Kincaid grade, not an approximation of one. The syllable heuristic is the only
part with judgement in it, so its documented cases are pinned here — including the ones
it is known to get wrong, so a future improvement shows up as a failing test rather than
a silent change in every stored ``readability_grade``.
"""

from __future__ import annotations

import math

import pytest

from opengloss_generator.readability import (
    flesch_kincaid_grade,
    grade_band,
    sentence_count,
    strip_markdown,
    syllables,
    word_count,
)
from opengloss_generator.schema import ReadingLevel


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("cat", 1),
        ("the", 1),
        ("a", 1),
        ("rope", 1),  # silent final e
        ("table", 2),  # -le is sounded
        ("tables", 2),
        ("wishes", 2),  # -es after a sibilant is sounded
        ("makes", 1),  # -es elsewhere is not
        ("appeared", 2),  # silent -ed
        ("wanted", 2),  # -ted is sounded
        ("friction", 2),
        ("acceleration", 5),
        ("professional", 4),  # adjacent vowels are one run: pro-fes-sio-nal
        ("area", 2),  # heuristic under-counts: really three
        ("don't", 1),
        ("grade_1", 1),  # non-letters are dropped before counting
    ],
)
def test_syllable_heuristic_matches_its_documented_cases(word, expected):
    assert syllables(word) == expected


def test_a_token_with_no_letters_is_scored_by_its_digits():
    assert syllables("2026") == 4
    assert syllables("---") == 0


def test_word_and_sentence_counts():
    text = "The cat sat on the mat. The dog ran to the park! We had fun."
    assert word_count(text) == 15
    assert sentence_count(text) == 3
    # A bare gloss has no terminal punctuation and is still one sentence.
    assert sentence_count("To descend a rock face using a rope") == 1
    assert sentence_count("") == 0
    assert word_count("") == 0


def test_flesch_kincaid_grade_matches_the_formula():
    text = "The cat sat on the mat."
    words, sentences, total_syllables = 6, 1, 6
    expected = 0.39 * (words / sentences) + 11.8 * (total_syllables / words) - 15.59
    assert flesch_kincaid_grade(text) == pytest.approx(expected)
    assert flesch_kincaid_grade(text) == pytest.approx(-1.45, abs=0.01)


def test_simple_prose_scores_far_below_technical_prose():
    simple = "Rub your hands together fast. They get warm. That pull is friction."
    technical = (
        "Friction is the tangential contact force opposing relative motion between two "
        "surfaces, proportional in the simplest model to the normal force between them."
    )
    assert flesch_kincaid_grade(simple) < 3.0
    assert flesch_kincaid_grade(technical) > 12.0


def test_an_empty_text_scores_zero():
    assert flesch_kincaid_grade("   ") == 0.0


def test_grade_bands_are_the_ones_the_prompt_promises():
    assert grade_band(ReadingLevel.GRADE_1) == (-math.inf, 3.0)
    assert grade_band(ReadingLevel.GRADE_5) == (3.0, 7.0)
    assert grade_band(ReadingLevel.GRADE_10) == (7.0, 12.0)
    assert grade_band(ReadingLevel.COLLEGE) == (10.0, math.inf)
    assert grade_band(ReadingLevel.NEUTRAL) == (-math.inf, math.inf)


def test_every_reading_level_has_a_band():
    for level in ReadingLevel:
        lower, upper = grade_band(level)
        assert lower < upper


def test_grade_band_reads_from_the_single_fk_bands_source_of_truth():
    # docs/STANDARDS-PLAN.md § 2, A6: readability.grade_band and the documented
    # CCSS/Lexile crosswalk both read from schema.FK_BANDS, so this check and that
    # documentation cannot disagree -- by construction, not by convention.
    from opengloss_generator.schema import FK_BANDS  # noqa: PLC0415

    assert set(FK_BANDS) == set(ReadingLevel)
    for level in ReadingLevel:
        assert grade_band(level) == FK_BANDS[level]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**people**", "people"),
        ("__people__", "people"),
        ("*plural of person*", "plural of person"),
        ("_plural of person_", "plural of person"),
        ("`m/s^2`", "m/s^2"),
        ("# A heading", "A heading"),
        ("- first\n- second", "first\nsecond"),
        ("1. first\n2. second", "first\nsecond"),
        ("> quoted line", "quoted line"),
        ("plain prose, untouched.", "plain prose, untouched."),
        # An underscore inside a word is not emphasis.
        ("reading_level=grade_1", "reading_level=grade_1"),
        # Multiplication and lone asterisks survive.
        ("3 * 4 is 12", "3 * 4 is 12"),
    ],
)
def test_strip_markdown(raw, expected):
    assert strip_markdown(raw) == expected


def test_strip_markdown_handles_a_mixed_passage():
    raw = "**Acceleration** is the *rate* of change of `velocity`.\n\n- it is a vector"
    assert (
        strip_markdown(raw) == "Acceleration is the rate of change of velocity.\n\nit is a vector"
    )
