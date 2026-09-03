"""The familiar-word metric that measures what Flesch-Kincaid cannot (D-51).

These are pure functions over text, so the tests are exact: the offending words are named,
not approximated. The lemmatiser is the only part with judgement in it, so its documented
cases are pinned here — including the ones it is known to get wrong, so a future
improvement shows up as a failing test rather than as a silent change in every stored
``hard_word_share``.
"""

from __future__ import annotations

import pytest

from opengloss_generator.schema import ReadingLevel
from opengloss_generator.vocabulary import (
    easy_words,
    exceeds_band,
    hard_word_share,
    hard_words,
    is_easy,
    lemma_candidates,
    vocabulary_band,
)

# The two sentences the QA judge marked as not level-appropriate although both pass their
# Flesch-Kincaid band -- the measurement this module exists to add (docs/QA-DIARY.md).
JUDGED_HARD = "Monks made vows of poverty, chastity, and obedience."
JUDGED_HARD_PROPER = "Ancient people in Mesopotamia, Greece, and Rome used oaths."


# --------------------------------------------------------------------------------------
# The word list
# --------------------------------------------------------------------------------------


def test_the_word_list_loads_and_is_the_size_dale_chall_promises():
    words = easy_words()
    assert len(words) >= 800
    # The Dale-Chall familiar-word list is ~3,000 words; the shipped file is that list
    # with its hyphenated entries also split into parts (see the file's own header).
    assert 2900 <= len(words) <= 3200


def test_the_word_list_is_lowercase_and_carries_no_comments():
    words = easy_words()
    assert all(word == word.lower() for word in words)
    assert not any(word.startswith("#") for word in words)
    for expected in ("promise", "machine", "government", "kid", "word", "use"):
        assert expected in words


def test_the_list_is_cached_so_the_file_is_read_once():
    assert easy_words() is easy_words()


# --------------------------------------------------------------------------------------
# Lemmatisation: the suffix stripper and its documented failure modes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "base"),
    [
        ("vows", "vow"),
        ("wishes", "wish"),
        ("cities", "city"),
        ("dog's", "dog"),
        ("stopped", "stop"),
        ("appeared", "appear"),
        ("carried", "carry"),
        ("making", "make"),
        ("running", "run"),
        ("bigger", "big"),
        ("happiest", "happy"),
        ("quickly", "quick"),
        ("happily", "happy"),
    ],
)
def test_the_lemmatiser_finds_the_base_form_of_a_regular_inflection(word, base):
    assert base in lemma_candidates(word)


def test_the_lemmatiser_keeps_the_word_itself_first():
    assert lemma_candidates("Vows")[0] == "vows"


def test_the_lemmatiser_misses_irregulars_as_documented():
    # "went" is not derived from "go"; both are on the list in their own right, which is
    # why the miss costs nothing in practice.
    assert "go" not in lemma_candidates("went")
    assert is_easy("went")


def test_an_inflection_of_a_familiar_word_is_familiar():
    for word in ("vows", "promised", "promising", "quickly", "biggest", "children's"):
        assert is_easy(word) or word == "vows"
    assert is_easy("promised")
    assert is_easy("kids")
    # "vow" is genuinely absent from the 1948 list, inflections included.
    assert not is_easy("vows")


# --------------------------------------------------------------------------------------
# The share itself
# --------------------------------------------------------------------------------------


def test_the_judged_sentence_measures_hard():
    share = hard_word_share(JUDGED_HARD)
    assert share > 0.5
    assert hard_words(JUDGED_HARD) == [
        "monks",
        "vows",
        "poverty",
        "chastity",
        "obedience",
    ]


def test_a_plain_definition_of_the_same_word_measures_easy():
    # The rewrite the judge would have accepted, with the headword excused as a definition
    # cannot avoid its own headword.
    text = "A vow is a very serious promise."
    assert hard_word_share(text, ignore=("vow",)) < 0.2
    assert hard_words(text, ignore=("vow",)) == ["serious"]
    assert hard_word_share("A big promise that you keep.", ignore=("vow",)) == 0.0


def test_proper_nouns_are_not_counted():
    # Mesopotamia, Greece and Rome are capitalised mid-sentence: names, not vocabulary
    # defects, and a rewrite cannot remove them from a passage that is about them.
    assert hard_words(JUDGED_HARD_PROPER) == ["ancient", "oaths"]
    assert hard_word_share(JUDGED_HARD_PROPER) == pytest.approx(2 / 6)


def test_a_sentence_initial_capital_is_still_counted():
    # "Obedience" opens the sentence, so its capital says nothing about it being a name.
    assert hard_words("Obedience was expected.") == ["obedience"]


def test_the_headword_and_its_forms_are_excused():
    text = "Photosynthesis feeds the plant. Plants use photosynthesis all day."
    assert "photosynthesis" not in hard_words(text, ignore=("photosynthesis",))
    # An inflection of the ignored term is excused too.
    assert hard_words("The vows were kept.", ignore=("vow",)) == []


def test_numbers_and_symbols_contribute_nothing():
    assert hard_word_share("3 * 4 = 12") == 0.0
    # "m" and "s" out of a unit are single letters, which are not counted as words.
    assert hard_word_share("2026 m/s^2") == 0.0
    assert hard_word_share("") == 0.0
    assert hard_words("   ") == []


def test_hard_words_are_unique_and_in_order():
    text = "Chastity and obedience and chastity again."
    assert hard_words(text) == ["chastity", "obedience"]


def test_the_share_counts_every_occurrence():
    # Two occurrences of one hard word in four counted words: the reader meets it twice.
    assert hard_word_share("Chastity and chastity now.") == pytest.approx(2 / 4)


# --------------------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------------------


def test_only_the_two_lowest_levels_have_a_band():
    assert vocabulary_band(ReadingLevel.GRADE_1) == 0.10
    assert vocabulary_band(ReadingLevel.GRADE_5) == 0.25
    assert vocabulary_band(ReadingLevel.GRADE_10) is None
    assert vocabulary_band(ReadingLevel.COLLEGE) is None
    assert vocabulary_band(ReadingLevel.NEUTRAL) is None


def test_every_reading_level_has_an_entry_so_a_new_level_cannot_be_forgotten():
    for level in ReadingLevel:
        vocabulary_band(level)  # raises KeyError if a level is missing


def test_exceeds_band_applies_the_tolerance_and_only_to_banded_levels():
    text = JUDGED_HARD
    assert exceeds_band(text, ReadingLevel.GRADE_1)
    assert exceeds_band(text, ReadingLevel.GRADE_5, tolerance=0.05)
    # No band at all: a college reader is expected to meet words they do not know.
    assert not exceeds_band(text, ReadingLevel.COLLEGE)
    assert not exceeds_band(text, ReadingLevel.NEUTRAL)
    easy = "A big promise that you keep for a long time."
    assert not exceeds_band(easy, ReadingLevel.GRADE_1)
