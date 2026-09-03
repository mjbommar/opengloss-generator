"""Deterministic word-familiarity measurement, beside the Flesch-Kincaid grade.

Flesch-Kincaid measures two things: how long the sentences are and how many syllables the
words have. It cannot see whether the words are *known*. A judged sample of the core
(docs/QA-DIARY.md, iteration 1) found **46.6% of grade_1 encyclopedia renditions not
level-appropriate while every one of them passed its FK band** — "Ancient people in
Mesopotamia, Greece, and Rome used oaths." and "Monks made vows of poverty, chastity, and
obedience." are eight- and nine-word sentences of one- and two-syllable words, which is
exactly what FK rewards. Grade-1 glosses failed at 10.6% and grade-1 examples at 7.8% for
the same reason.

This module is the second, complementary measurement: what share of a passage's words are
*not* on a familiar-word list. It is the Dale-Chall idea, which is older than FK's
critics and has always been the other half of the pair — Dale-Chall's own formula is a
weighted sum of a sentence-length term and precisely this share.

The list is ``data/easy_words.txt``: the Dale-Chall familiar-word list of ~3,000 words a
fourth-grade reader recognises, sourced and normalised as that file's own header records
(D-51).

What is counted
---------------

Every alphabetic token of the text, except three kinds that are deliberately not the
reader's problem:

* the **headword** and its forms, passed in ``ignore``. A definition cannot avoid the word
  it defines, and "photosynthesis" would otherwise make every rendition of that entry
  fail at every level.
* **proper nouns**, taken to be tokens capitalised anywhere but at the start of a
  sentence. Dale-Chall's own scoring rules treat proper nouns as familiar; more to the
  point here, a rewrite cannot remove "Mesopotamia" from a passage about Mesopotamia — it
  can only explain it, which is a judgement no word list can make.
* **numbers and symbols**, which the reading-level constraints in
  :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` already govern and the FK
  measurement already prices. A single letter that is not itself a word goes with them:
  "m" and "s" out of ``m/s^2`` are a unit, not two unknown words. "a" and "I" are on the
  list and count like any other familiar word.

A token counts as familiar when it, or any of the base forms
:func:`lemma_candidates` derives from it, is on the list — Dale-Chall counts regular
inflections of a familiar word as familiar, so "vows", "vowed", "quickly" and "biggest"
are as easy as "vow", "quick" and "big".

Known failure modes
-------------------

* **The list is from 1948 (revised 1995) and is not exhaustive**: it has "promise",
  "machine" and "government" but not "serious", "problem" or "area". Those three measure
  as hard words. The band is set with :attr:`~opengloss_generator.config.ReadabilityConfig.
  vocabulary_tolerance` on top of it precisely to absorb that noise, and the metric is
  used as a *share* over a whole passage rather than as a verdict on a single word.
* **The lemmatiser is a suffix stripper, not a morphological analyser.** It gets the
  regular patterns and the common spelling changes (``-ies``/``-ied`` -> ``y``, silent
  ``e`` restored before ``-ing``/``-ed``, a doubled final consonant undone) and misses
  irregulars entirely: "went", "children", "better" and "worse" are not derived from
  "go", "child" and "good", so they are judged on their own entries in the list (the
  list happens to carry all four). It also over-strips: "ring" -> "r" is a candidate,
  which is harmless because a wrong candidate can only ever be absent from the list, and
  "sing"/"thing" are on it in their own right. Over-stripping can produce a false
  *familiar* verdict when a stripped form collides with a listed word ("bores" ->
  "bore"), which is the direction that costs nothing.
* **Capitalisation is the only proper-noun signal.** A sentence-initial proper noun
  ("Mesopotamia was a place.") is counted like any other word, and an all-capitals
  acronym mid-sentence is skipped as if it were a name.
* **A very short text is a coarse measurement.** One hard word in a seven-word gloss is a
  0.14 share. That is intended — one unknown word in a one-sentence definition really is
  what a six-year-old trips on — but it means the metric is noisier per rendition than
  per passage.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from importlib import resources

from opengloss_generator.schema import ReadingLevel

__all__ = [
    "VOCABULARY_BANDS",
    "easy_words",
    "exceeds_band",
    "hard_word_share",
    "hard_words",
    "is_easy",
    "lemma_candidates",
    "vocabulary_band",
]

#: The package-relative location of the familiar-word list. Read through
#: :mod:`importlib.resources` rather than ``__file__`` so it is found in an installed
#: wheel as well as in a source checkout.
_WORD_LIST_PACKAGE = "opengloss_generator"
_WORD_LIST_PARTS = ("data", "easy_words.txt")

#: The largest share of unfamiliar words a rendition at each reading level may carry.
#: ``None`` means the level is not checked at all: grade_10 and college readers are
#: expected to meet words they do not know, and a neutral rendition has no audience to
#: fail. The two numbers are the ones D-51 set — a tenth of a grade_1 passage and a
#: quarter of a grade_5 one — read together with
#: :attr:`~opengloss_generator.config.ReadabilityConfig.vocabulary_tolerance`, which is
#: what actually absorbs the list's own gaps.
VOCABULARY_BANDS: dict[ReadingLevel, float | None] = {
    ReadingLevel.GRADE_1: 0.10,
    ReadingLevel.GRADE_5: 0.25,
    ReadingLevel.GRADE_10: None,
    ReadingLevel.COLLEGE: None,
    ReadingLevel.NEUTRAL: None,
}

# A token is a run of letters, apostrophes included so "don't" and "you're" stay whole.
# It must begin with a letter, so "2026" and "m/s^2" contribute no tokens at all.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019]*")

# Characters that end a sentence, so the next token is sentence-initial and its capital
# letter says nothing about whether it is a name.
_SENTENCE_ENDINGS = frozenset(".!?\u2026:;\n\r")
# Characters skipped when looking back for the end of the previous sentence: an opening
# quote or bracket sits between the terminator and the next word.
_SKIPPED_BEFORE_TOKEN = frozenset(" \t\"'([{-\u2018\u201c\u2013\u2014")

_APOSTROPHES = "'\u2019"

#: The shortest token that counts as a word at all; see :func:`_scan`.
_MIN_COUNTED_LENGTH = 2


@lru_cache(maxsize=1)
def easy_words() -> frozenset[str]:
    """Return the familiar-word list, loaded once and cached.

    Returns:
        Every word of ``data/easy_words.txt``, lowercased. Comment lines (``#``) and
        blank lines are dropped.
    """
    raw = (
        resources.files(_WORD_LIST_PACKAGE).joinpath(*_WORD_LIST_PARTS).read_text(encoding="utf-8")
    )
    return frozenset(
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    )


#: The shortest candidate base form worth testing. A one-letter stem is never a word on
#: the list and only makes the candidate set longer.
_MIN_CANDIDATE_LENGTH = 2
#: The shortest stem in which a doubled final consonant can be undone: "add" must stay
#: "add", while "stopp" becomes "stop".
_MIN_UNDOUBLE_LENGTH = 3

# Endings after which the ``e`` of ``-es`` is a syllable of its own, so the plural is
# formed by adding ``-es`` to a stem that keeps its final letter ("wishes" -> "wish").
_SIBILANT_ES = ("ses", "xes", "zes", "ches", "shes")


def lemma_candidates(word: str) -> tuple[str, ...]:
    """Return the base forms a word might be an inflection of, the word itself first.

    A deliberately cheap inverse of
    :func:`~opengloss_generator.spans.generate_forms`: that function adds the regular
    English endings, this one takes them off again. Handled, each with the spelling
    changes the ending implies:

    * possessive ``'s`` (``dog's`` -> ``dog``)
    * ``-s``/``-es``/``-ies`` (``vows`` -> ``vow``, ``wishes`` -> ``wish``,
      ``cities`` -> ``city``)
    * ``-ed``/``-d``/``-ied``, plus an undoubled final consonant (``stopped`` -> ``stop``)
    * ``-ing``, plus a restored silent ``e`` and an undoubled consonant
      (``making`` -> ``make``, ``running`` -> ``run``)
    * ``-er``/``-est``/``-ier``/``-iest`` (``bigger`` -> ``big``, ``happiest`` -> ``happy``)
    * ``-ly``/``-ily`` (``quickly`` -> ``quick``, ``happily`` -> ``happy``)

    Candidates are *guesses*, not analyses: several are produced for one word and a wrong
    one is simply absent from the familiar-word list. See the module docstring for what
    that costs and what it cannot do.

    Args:
        word: One token, in any case.

    Returns:
        The lowercased word followed by its distinct candidate base forms, in a stable
        order. Empty only for a token with no letters left after the apostrophes are
        stripped.
    """
    lowered = word.lower().strip(_APOSTROPHES)
    if not lowered:
        return ()
    stem = lowered
    for apostrophe in _APOSTROPHES:
        if stem.endswith(f"{apostrophe}s"):
            stem = stem[:-2]
            break

    candidates: list[str] = [lowered]
    for form in (
        stem,
        *_plural_candidates(stem),
        *_verb_candidates(stem),
        *_degree_candidates(stem),
        *_adverb_candidates(stem),
    ):
        if len(form) >= _MIN_CANDIDATE_LENGTH and form not in candidates:
            candidates.append(form)
    return tuple(candidates)


def _plural_candidates(stem: str) -> tuple[str, ...]:
    """Return the base forms a plural or third-person ``-s`` form could come from."""
    if stem.endswith("ies"):
        return (stem[:-3] + "y", stem[:-1], stem[:-2])
    if stem.endswith(_SIBILANT_ES):
        return (stem[:-2], stem[:-1])
    if stem.endswith("es"):
        return (stem[:-1], stem[:-2])
    if stem.endswith("s") and not stem.endswith("ss"):
        return (stem[:-1],)
    return ()


def _verb_candidates(stem: str) -> tuple[str, ...]:
    """Return the base forms an ``-ed`` or ``-ing`` form could come from."""
    if stem.endswith("ied"):
        return (stem[:-3] + "y", stem[:-1], stem[:-2])
    if stem.endswith("ed"):
        return (stem[:-1], stem[:-2], _undouble(stem[:-2]))
    if stem.endswith("ing"):
        return (stem[:-3], stem[:-3] + "e", _undouble(stem[:-3]))
    return ()


def _degree_candidates(stem: str) -> tuple[str, ...]:
    """Return the base forms a comparative or superlative could come from."""
    if stem.endswith("iest"):
        return (stem[:-4] + "y",)
    if stem.endswith("ier"):
        return (stem[:-3] + "y",)
    if stem.endswith("est"):
        return (stem[:-3], stem[:-2], _undouble(stem[:-3]))
    if stem.endswith("er"):
        return (stem[:-2], stem[:-1], _undouble(stem[:-2]))
    return ()


def _adverb_candidates(stem: str) -> tuple[str, ...]:
    """Return the base forms an ``-ly`` adverb could come from."""
    if stem.endswith("ily"):
        return (stem[:-3] + "y",)
    if stem.endswith("ly"):
        return (stem[:-2],)
    return ()


def _undouble(stem: str) -> str:
    """Return a stem with a doubled final consonant reduced to one (``stopp`` -> ``stop``)."""
    if len(stem) >= _MIN_UNDOUBLE_LENGTH and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
        return stem[:-1]
    return stem


def is_easy(word: str) -> bool:
    """Return whether a word is on the familiar-word list, in any regular inflection.

    Args:
        word: One token, in any case.

    Returns:
        Whether the word itself or any of :func:`lemma_candidates`'s base forms is listed.
    """
    listed = easy_words()
    return any(candidate in listed for candidate in lemma_candidates(word))


def _ignored_forms(ignore: Iterable[str]) -> frozenset[str]:
    """Return every lowercased form the ``ignore`` terms should match.

    Each term contributes its own words and each of their lemma candidates, so passing a
    headword also excuses its plural and its past tense.

    Args:
        ignore: Terms not counted at all, typically the entry's headword.

    Returns:
        The set of forms a token is excused by.
    """
    forms: set[str] = set()
    for term in ignore:
        for token in _TOKEN_RE.findall(term):
            forms.update(lemma_candidates(token))
    return frozenset(forms)


def _is_sentence_initial(text: str, start: int) -> bool:
    """Return whether the token at ``start`` opens a sentence.

    Args:
        text: The whole text.
        start: The token's start offset.

    Returns:
        ``True`` when only whitespace, opening quotes or brackets separate the token from
        the start of the text or from a sentence terminator.
    """
    index = start - 1
    while index >= 0 and text[index] in _SKIPPED_BEFORE_TOKEN:
        index -= 1
    return index < 0 or text[index] in _SENTENCE_ENDINGS


def _scan(text: str, ignore: Iterable[str]) -> tuple[list[str], int]:
    """Return the unfamiliar tokens of a text and how many tokens were considered.

    Args:
        text: The text to scan.
        ignore: Terms not counted at all, matched whole-word and case-insensitively
            through their lemma candidates.

    Returns:
        ``(hard tokens in order, considered token count)``. Repeats appear in the list
        once per occurrence, since a reader meets the word each time.
    """
    excused = _ignored_forms(ignore)
    listed = easy_words()
    hard: list[str] = []
    considered = 0
    for match in _TOKEN_RE.finditer(text):
        token = match.group()
        if len(token) < _MIN_COUNTED_LENGTH and token.lower() not in listed:
            # A lone letter that is not itself a word is a unit symbol or an initial ("m"
            # and "s" out of m/s^2), not a word a reader knows or does not know. The two
            # that are words, "a" and "I", are on the list and count normally.
            continue
        candidates = lemma_candidates(token)
        if not candidates or excused.intersection(candidates):
            continue
        if token[0].isupper() and not _is_sentence_initial(text, match.start()):
            # A capital in mid-sentence is a name, and a name is not a vocabulary defect.
            continue
        considered += 1
        if not any(candidate in listed for candidate in candidates):
            hard.append(token.lower())
    return hard, considered


def hard_word_share(text: str, *, ignore: Iterable[str] = ()) -> float:
    """Return the share of a text's words that are not on the familiar-word list.

    Args:
        text: The text to measure. Markdown should already be stripped from it.
        ignore: Terms not counted at all, typically the entry's headword; matched
            whole-word, case-insensitively, through their regular inflections.

    Returns:
        A share between 0.0 and 1.0. A text with no countable words scores 0.0 — there is
        nothing in it for a reader to trip on.
    """
    hard, considered = _scan(text, ignore)
    if not considered:
        return 0.0
    return len(hard) / considered


def hard_words(text: str, *, ignore: Iterable[str] = ()) -> list[str]:
    """Return the unfamiliar words of a text, lowercased, in order and without repeats.

    This is what the model is shown when it is asked to rewrite: naming the words is what
    makes the feedback actionable, where "use simpler words" reliably returns the same
    passage with two words changed.

    Args:
        text: The text to inspect.
        ignore: Terms not counted at all; see :func:`hard_word_share`.

    Returns:
        Each offending word once, in first-appearance order.
    """
    hard, _ = _scan(text, ignore)
    seen: dict[str, None] = {}
    for word in hard:
        seen.setdefault(word, None)
    return list(seen)


def vocabulary_band(level: ReadingLevel) -> float | None:
    """Return the largest unfamiliar-word share a reading level allows.

    Args:
        level: The reading level a rendition targets.

    Returns:
        The band from :data:`VOCABULARY_BANDS` — 0.10 for ``grade_1``, 0.25 for
        ``grade_5`` — or ``None`` for a level this check does not apply to.
    """
    return VOCABULARY_BANDS[level]


def exceeds_band(
    text: str,
    level: ReadingLevel,
    *,
    tolerance: float = 0.0,
    ignore: Sequence[str] = (),
) -> bool:
    """Return whether a text carries more unfamiliar words than its level allows.

    Args:
        text: The text to measure.
        level: The reading level it was written for.
        tolerance: Added to the band before the comparison, absorbing the word list's own
            gaps (see the module docstring).
        ignore: Terms not counted at all; see :func:`hard_word_share`.

    Returns:
        ``False`` for any level with no band, whatever the text measures.
    """
    band = vocabulary_band(level)
    if band is None:
        return False
    return hard_word_share(text, ignore=ignore) > band + tolerance
