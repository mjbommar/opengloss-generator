"""Deterministic readability measurement.

A reading-level rendition is only as good as the level it actually hits. The model is
told what "grade 1" means in :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS`,
but telling is not measuring: iteration 1 of the core pass produced a "grade 1"
encyclopedia entry containing ``m/s^2`` and ``a = dv/dt``. So every rendition is scored
here, the score is stored on :class:`~opengloss_generator.schema.Assessment`, and the two
lowest levels are regenerated once when the score misses its band
(``workflows/enrich.py``).

The metric is Flesch-Kincaid grade level::

    0.39 x (words / sentences) + 11.8 x (syllables / words) - 15.59

It is used because it is cheap, stable, and needs nothing but the text — a model call to
judge readability would cost more than the rewrite it is judging. It is a *proxy*: it
cannot see whether the vocabulary is concrete or whether a formula survived, so the
prompt constraints and this measurement are complementary, not redundant.

The proxy's blind spot is *word familiarity*, and it is a large one: a judged sample found
46.6% of grade_1 encyclopedia renditions not level-appropriate although every one of them
passed the band here (docs/QA-DIARY.md). "Monks made vows of poverty, chastity, and
obedience." is a nine-word sentence of one- and two-syllable words, which is exactly what
this formula rewards. :mod:`opengloss_generator.vocabulary` measures the other half — the
share of a text's words that are not on a familiar-word list — and its three entry points
are re-exported here so a caller that wants "how hard is this text" gets both halves from
one import (D-51).

Syllable counting is heuristic (English has no closed-form rule). The heuristic and its
known failure modes are documented on :func:`syllables`.

:func:`grade_band` reads its per-level bands from
:data:`opengloss_generator.schema.FK_BANDS` rather than keeping its own copy
(docs/STANDARDS-PLAN.md § 2, A6): that table sits next to
:data:`~opengloss_generator.schema.READING_LEVEL_CROSSWALK`, so the acceptance check and
the documented CCSS/Lexile crosswalk are read from one place and cannot drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from opengloss_generator.schema import FK_BANDS, ReadingLevel
from opengloss_generator.vocabulary import hard_word_share, hard_words, vocabulary_band

__all__ = [
    "flesch_kincaid_grade",
    "grade_band",
    "hard_word_share",
    "hard_words",
    "sentence_count",
    "strip_markdown",
    "syllables",
    "vocabulary_band",
    "word_count",
]

_VOWELS = frozenset("aeiouy")

# A word is a run of word characters, apostrophes included so "don't" is one token and
# "grade_1" is not split into two. Runs with no alphanumeric content are not words.
_TOKEN_RE = re.compile(r"[\w'\u2019]+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?…]+")

# Word endings whose final ``e`` is pronounced, or where the ``e`` of ``-es``/``-ed`` is
# a syllable of its own. Everything else ending in a silent ``e`` loses one group.
_SOUNDED_FINAL_E = ("le", "ee", "ye", "oe", "ie")
_SOUNDED_ES = ("ses", "xes", "zes", "ches", "shes", "ges", "ces", "les")
_SOUNDED_ED = ("ted", "ded")

_FK_WORDS_PER_SENTENCE = 0.39
_FK_SYLLABLES_PER_WORD = 11.8
_FK_CONSTANT = 15.59


def syllables(word: str) -> int:
    """Estimate how many syllables one word has.

    The heuristic, applied to the word lowercased and reduced to letters:

    1. Count maximal runs of vowel letters (``a e i o u y``); each run is one syllable.
    2. Drop one for a silent final ``e`` (``rope`` -> 1), unless the word ends in a
       sounded pair such as ``-le`` (``table`` -> 2), ``-ee``, ``-ye``, ``-oe``, ``-ie``,
       or unless it would leave nothing (``the`` -> 1).
    3. Drop one for a final ``-es``/``-ed`` whose ``e`` is silent (``appeared`` -> 2),
       keeping the ones where it is not (``wishes``, ``tables``, ``wanted``).
    4. Never return less than 1 for a word that has any letters.

    A token with no letters is scored by its digits (``"2026"`` -> 4), which keeps a
    numeral from being free; a token with neither letters nor digits is not a word and
    scores 0.

    Known failures: it under-counts words whose adjacent vowels are separate syllables
    (``area``, ``poem``) and mis-handles some ``-ed`` endings after ``r`` clusters. Across
    a sentence these errors are small and unbiased, which is all the grade formula needs.

    Args:
        word: One whitespace-delimited token.

    Returns:
        The estimated syllable count.
    """
    letters = "".join(ch for ch in word.lower() if ch.isalpha())
    if not letters:
        digits = sum(1 for ch in word if ch.isdigit())
        return digits
    groups = 0
    previous_was_vowel = False
    for char in letters:
        is_vowel = char in _VOWELS
        if is_vowel and not previous_was_vowel:
            groups += 1
        previous_was_vowel = is_vowel
    if groups > 1 and _has_silent_final_e(letters):
        groups -= 1
    return max(1, groups)


def _has_silent_final_e(letters: str) -> bool:
    """Return whether a word's final ``e`` is unvoiced and so spends no syllable."""
    if letters.endswith("es"):
        return not letters.endswith(_SOUNDED_ES)
    if letters.endswith("ed"):
        return not letters.endswith(_SOUNDED_ED)
    return letters.endswith("e") and not letters.endswith(_SOUNDED_FINAL_E)


def _words(text: str) -> list[str]:
    """Return the word tokens of a text, in order."""
    return [token for token in _TOKEN_RE.findall(text) if any(ch.isalnum() for ch in token)]


def word_count(text: str) -> int:
    """Return how many words a text contains.

    Args:
        text: The text to measure.
        ignore: Terms scored as one syllable, whole-word and case-insensitive. A
            definition cannot avoid its own headword, and a five-syllable headword in an
            eight-word sentence would otherwise put a simple gloss two grades too high.

    Returns:
        The number of tokens carrying at least one letter or digit.
    """
    return len(_words(text))


def sentence_count(text: str) -> int:
    """Return how many sentences a text contains.

    Sentences are delimited by ``.``, ``!``, ``?`` and ``…``. A text with words but no
    terminal punctuation counts as one sentence, which is what a bare gloss or a rewritten
    example usually is. Abbreviations containing periods will over-count; the renditions
    prompt forbids abbreviations at the two levels where the count is acted on.

    Args:
        text: The text to measure.

    Returns:
        The number of sentences, or 0 for a text with no words.
    """
    if not _words(text):
        return 0
    parts = [part for part in _SENTENCE_SPLIT_RE.split(text) if _words(part)]
    return max(1, len(parts))


def flesch_kincaid_grade(text: str, *, ignore: Iterable[str] = ()) -> float:
    """Return the Flesch-Kincaid grade level of a text.

    Args:
        text: The text to score.
        ignore: Terms scored as one syllable, whole-word and case-insensitive. A
            definition cannot avoid its own headword, and a five-syllable headword in an
            eight-word sentence would otherwise put a simple gloss two grades too high.

    Returns:
        The grade level, unrounded and unclamped: it can be negative for very simple
        text, which is meaningful (a lower number is simpler). An empty text scores 0.0.
    """
    for term in ignore:
        if term.strip():
            text = re.sub(rf"\b{re.escape(term.strip())}\b", "it", text, flags=re.IGNORECASE)
    words = _words(text)
    if not words:
        return 0.0
    sentences = max(1, sentence_count(text))
    total_syllables = sum(syllables(word) for word in words) or len(words)
    return (
        _FK_WORDS_PER_SENTENCE * (len(words) / sentences)
        + _FK_SYLLABLES_PER_WORD * (total_syllables / len(words))
        - _FK_CONSTANT
    )


def grade_band(level: ReadingLevel) -> tuple[float, float]:
    """Return the acceptable Flesch-Kincaid band for a reading level.

    Args:
        level: The reading level a rendition targets.

    Returns:
        ``(lower, upper)`` bounds, inclusive, either of which may be infinite, read from
        :data:`opengloss_generator.schema.FK_BANDS`: ``grade_1`` is at most 3.0,
        ``grade_5`` is 3.0 to 7.0, ``grade_10`` is 7.0 to 12.0, ``college`` is at least
        10.0, and ``neutral`` is unbounded.
    """
    return FK_BANDS[level]


# --------------------------------------------------------------------------------------
# Markdown hygiene
# --------------------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~).*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]{0,8}(?:[-*+]|\d{1,3}[.)])[ \t]+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_EMPHASIS_STAR_RE = re.compile(r"(?<!\*)\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)")
_EMPHASIS_UNDERSCORE_RE = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_BACKTICK_RE = re.compile(r"`+")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Return a text with markdown formatting removed, leaving plain prose.

    Renditions are stored as prose and read by consumers that do not render markdown, so
    ``**people**`` must arrive as ``people``. Handled: fenced code markers, heading
    hashes, leading list bullets and numbered-list markers, blockquote markers,
    ``**bold**``/``__bold__``, ``*italic*``/``_italic_``, and backticks. An underscore
    inside a word is left alone, so ``grade_1`` survives.

    Args:
        text: Possibly-formatted text.

    Returns:
        The same text with the markers removed and surrounding whitespace stripped.
    """
    cleaned = _CODE_FENCE_RE.sub("", text)
    cleaned = _HEADING_RE.sub("", cleaned)
    cleaned = _BLOCKQUOTE_RE.sub("", cleaned)
    cleaned = _BULLET_RE.sub("", cleaned)
    cleaned = _STRONG_RE.sub(r"\2", cleaned)
    cleaned = _EMPHASIS_STAR_RE.sub(r"\1", cleaned)
    cleaned = _EMPHASIS_UNDERSCORE_RE.sub(r"\1", cleaned)
    cleaned = _BACKTICK_RE.sub("", cleaned)
    cleaned = _TRAILING_SPACE_RE.sub("", cleaned)
    return cleaned.strip()
