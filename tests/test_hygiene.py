"""The deterministic near-copy measurement (D-59, F7).

``content_words``, ``lexical_diversity`` and ``is_near_copy`` are pure functions over
text, so the tests are exact: a hand-counted content-word overlap, not an approximation
of one.
"""

from __future__ import annotations

from opengloss_generator.hygiene import (
    NEAR_COPY_JACCARD_THRESHOLD,
    content_words,
    is_near_copy,
    lexical_diversity,
)


def test_content_words_drops_stopwords_and_lowercases():
    text = "The Quick Brown fox jumps over a lazy dog and the cat."
    assert content_words(text) == {"quick", "brown", "fox", "jumps", "lazy", "dog", "cat"}


def test_content_words_drops_numbers_and_punctuation():
    # "It" and "or" are themselves stopwords, so only "costs" survives.
    assert content_words("It costs $3.50, or 12%!") == {"costs"}


def test_content_words_of_an_empty_or_punctuation_only_text_is_empty():
    assert content_words("") == set()
    assert content_words("... !! ??") == set()


def test_lexical_diversity_of_identical_texts_is_zero():
    text = "A strong rope holds you steady on the cliff."
    assert lexical_diversity(text, text) == 0.0


def test_lexical_diversity_of_completely_disjoint_texts_is_one():
    a = "Monks made vows of poverty."
    b = "The bicycle rim warmed up under braking."
    assert lexical_diversity(a, b) == 1.0


def test_lexical_diversity_is_one_minus_jaccard_over_content_words():
    # {rope, holds, cliff} vs {rope, holds, mountain}: intersection 2, union 4.
    a = "A rope holds you on the cliff."
    b = "A rope holds you on the mountain."
    assert lexical_diversity(a, b) == 1.0 - 2 / 4


def test_lexical_diversity_of_two_empty_content_word_sets_is_zero_by_convention():
    # Both texts are pure stopwords/punctuation, so both content-word sets are empty.
    assert lexical_diversity("The a an.", "An a the!") == 0.0


def test_lexical_diversity_is_symmetric():
    a = "A rope holds you on the cliff."
    b = "A harness keeps you safe on the mountain."
    assert lexical_diversity(a, b) == lexical_diversity(b, a)


def test_near_copy_threshold_is_ninety_percent_jaccard():
    assert NEAR_COPY_JACCARD_THRESHOLD == 0.9


def test_is_near_copy_true_for_a_verbatim_rewrite():
    source = "A deadline is the time by which a task must be finished."
    assert is_near_copy(source, source)


def test_is_near_copy_true_for_a_one_word_swap_in_a_longer_sentence():
    # 19 of 20 content words are shared: 19/21 = 0.905 Jaccard (intersection over
    # intersection-plus-both-unique-words), comfortably above the 0.9 floor -- one
    # swapped word is exactly the "synonym or two" defect D-59 targets, not a genuine
    # rewrite.
    shared = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "lima mike november oscar papa quebec romeo sierra tango"
    )
    source = f"{shared} original"
    swapped = f"{shared} replaced"
    assert lexical_diversity(source, swapped) < 1.0 - NEAR_COPY_JACCARD_THRESHOLD
    assert is_near_copy(source, swapped)


def test_is_near_copy_false_for_a_genuine_rewrite():
    source = "A deadline is the time by which a task must be finished."
    rewrite = "Whatever you are working on, it has to be wrapped up by then."
    assert not is_near_copy(source, rewrite)


def test_is_near_copy_respects_an_explicit_threshold():
    a = "A rope holds you on the cliff."
    b = "A rope holds you on the mountain."
    similarity = 1.0 - lexical_diversity(a, b)
    assert is_near_copy(a, b, threshold=similarity)
    assert not is_near_copy(a, b, threshold=similarity + 0.01)
