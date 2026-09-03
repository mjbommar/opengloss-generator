"""Deterministic headword span finder.

See ``docs/SCHEMA-V3.md`` § 3 for the contract. This module locates the character
offsets of a headword (or one of its inflected/derived forms) inside an example
sentence, without any model call. It is intentionally a pure-string module: it has
no dependency on ``schema.py`` or any other project module, so it can be built and
tested independently of the rest of the v3 rewrite.

Matching is case-insensitive and whole-word, and is done with ``re.IGNORECASE``
directly against the *original* text rather than by searching a lowercased or
``casefold``-ed copy and translating indices back. That distinction matters:
``str.casefold()`` can change a string's length (e.g. ``"straße".casefold() ==
"strasse"``, one character longer), so an index found in a casefolded copy does not
reliably correspond to the same offset in the original string once such a
character has been consumed. Matching in place with ``re.IGNORECASE`` sidesteps the
problem entirely: whatever the engine matches, its ``.span()`` already indexes the
original text.

What this module deliberately does NOT handle deterministically (and therefore
leaves for the LLM fallback stage described in § 5 of the schema doc):

* Irregular inflections not supplied via ``forms`` (e.g. "run" -> "ran", "make" ->
  "made"). ``generate_forms`` only produces regular, rule-based forms.
* Headwords that are themselves affixes with a leading or trailing hyphen (e.g.
  "-ing", "un-"), since a literal hyphen at the edge of the pattern defeats a
  plain word-boundary assertion. Such lexemes are rare (``LexemeKind.AFFIX``) and
  are expected to be resolved by the fallback.
* Non-English morphology, ligatures, or any casefold expansion that changes the
  number of matched characters (e.g. German "ß" versus "ss"); the pattern is
  matched against the literal spelling supplied, not a normalized one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["find_span", "generate_forms", "unresolved"]

# Splitting/joining points for the hyphen/space/underscore-insensitive multi-word
# match: whichever separator the caller happened to use when writing the headword
# ("ice axe", "ice-axe", "ice_axe") is treated as equivalent, and the compiled
# pattern accepts any of the three (or a run of them) in the target text too.
_SEPARATOR_SPLIT_RE = re.compile(r"[\s\-_]+")
_SEPARATOR_PATTERN = r"[\s\-_]+"

_VOWELS = frozenset("aeiou")
_NO_DOUBLE_FINAL = frozenset("wxy")
_SIBILANT_SUFFIXES = ("s", "x", "z", "ch", "sh")
_MIN_CVC_LENGTH = 3
_MIN_Y_RULE_LENGTH = 2


def _build_pattern(candidate: str) -> re.Pattern[str] | None:
    """Compile a whole-word, separator-flexible pattern for one candidate term.

    Args:
        candidate: A headword or inflected/derived form.

    Returns:
        A compiled, case-insensitive pattern, or ``None`` if the candidate has no
        matchable content (empty or all-separator).
    """
    tokens = [token for token in _SEPARATOR_SPLIT_RE.split(candidate.strip()) if token]
    if not tokens:
        return None
    body = _SEPARATOR_PATTERN.join(re.escape(token) for token in tokens)
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def find_span(text: str, headword: str, forms: Iterable[str] = ()) -> tuple[int, int] | None:
    """Find the character span of a headword or form inside example text.

    Every candidate -- the headword itself and each supplied form -- is matched
    case-insensitively and whole-word (apostrophes and letters immediately after
    the match block a match, so "abseiling" never matches headword "abseil", but
    ordinary punctuation and the possessive "'s" do not: "abseil's" matches
    "abseil"). Multi-word candidates additionally tolerate any mix of hyphen,
    space, or underscore between their words, in either the candidate or the
    text, so "ice axe" matches "ice-axe" and "Ice  Axe".

    Among all matches found across all candidates, the longest span wins; ties
    (equal length) are broken by earliest occurrence in ``text``.

    Args:
        text: The example sentence to search. Offsets in the result index this
            exact string.
        headword: The dictionary form to look for first.
        forms: Additional inflected or derived surface forms to also try (for
            example, from a ``Morphology`` block or ``generate_forms``).

    Returns:
        A ``(start, end)`` tuple of character offsets such that
        ``text[start:end]`` is the matched span, or ``None`` if no candidate
        matched anywhere in ``text``.
    """
    if not text:
        return None

    best: tuple[int, int] | None = None
    for candidate in (headword, *forms):
        pattern = _build_pattern(candidate)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if best is None:
                best = (start, end)
                continue
            best_start, best_end = best
            length, best_length = end - start, best_end - best_start
            if length > best_length or (length == best_length and start < best_start):
                best = (start, end)
    return best


def unresolved(examples: Iterable[tuple[str, str, Iterable[str]]]) -> list[int]:
    """Return the indices of examples that ``find_span`` could not place.

    Callers pass ``(text, headword, forms)`` triples; the returned indices are
    positions in that same sequence, so they can be sent to the LLM span-fallback
    stage (§ 5, batches of 40) exactly as-is.

    Args:
        examples: An iterable of ``(text, headword, forms)`` triples.

    Returns:
        Indices, in order, of triples for which ``find_span`` returned ``None``.
    """
    return [
        index
        for index, (text, headword, forms) in enumerate(examples)
        if find_span(text, headword, forms) is None
    ]


def _is_cvc(word: str) -> bool:
    """Return whether ``word`` ends in a doubling-eligible consonant-vowel-consonant.

    Used to decide whether the final consonant should be doubled before an "-ing"
    or "-ed" suffix (e.g. "run" -> "running"). Final "w", "x", or "y" are excluded
    per standard English orthography (e.g. "box" -> "boxing", not "boxxing").

    Args:
        word: The lowercased headword to inspect.

    Returns:
        ``True`` if the last three letters follow the consonant-vowel-consonant
        pattern and the final letter is eligible for doubling.
    """
    if len(word) < _MIN_CVC_LENGTH:
        return False
    last, mid, prior = word[-1], word[-2], word[-3]
    return (
        last not in _VOWELS
        and last not in _NO_DOUBLE_FINAL
        and mid in _VOWELS
        and prior not in _VOWELS
    )


def _ends_in_y_after_consonant(lowered: str) -> bool:
    """Return whether ``lowered`` ends in "y" preceded by a consonant."""
    return len(lowered) >= _MIN_Y_RULE_LENGTH and lowered[-1] == "y" and lowered[-2] not in _VOWELS


def _plural_like(headword: str, lowered: str, *, ends_in_y: bool) -> str:
    """Return the "-s"/"-es"/"-ies" candidate for ``headword``."""
    if ends_in_y:
        return headword[:-1] + "ies"
    if lowered.endswith(_SIBILANT_SUFFIXES):
        return headword + "es"
    return headword + "s"


def _past_like(headword: str, *, ends_in_y: bool, ends_in_e: bool, doubles: bool) -> str:
    """Return the "-ed"/"-d"/"-ied" candidate for ``headword``."""
    if ends_in_y:
        return headword[:-1] + "ied"
    if ends_in_e:
        return headword + "d"
    if doubles:
        return headword + headword[-1] + "ed"
    return headword + "ed"


def _ing_form(headword: str, *, ends_in_e: bool, doubles: bool) -> str:
    """Return the "-ing" candidate for ``headword``."""
    if ends_in_e:
        return headword[:-1] + "ing"
    if doubles:
        return headword + headword[-1] + "ing"
    return headword + "ing"


def generate_forms(headword: str) -> tuple[str, ...]:
    """Generate cheap, deterministic English inflections of a headword.

    This is a heuristic, not a morphological analyzer: it covers the regular
    patterns (plural/3rd-person "-s"/"-es", past tense "-ed"/"-d", "-ing", final-
    consonant doubling for short consonant-vowel-consonant words, and "y" ->
    "ies"/"ied" after a consonant) and will produce wrong or nonsensical forms for
    irregular verbs and nouns (e.g. "run" -> "runned", not "ran"). Whenever a
    ``Morphology`` block from the model is available for a lexeme, prefer it over
    this function; ``generate_forms`` exists only as a zero-cost fallback so
    ``find_span`` still has something to try.

    Args:
        headword: The dictionary form to inflect. Matching is done on the
            lowercased form; the returned forms preserve the original casing of
            ``headword``.

    Returns:
        A tuple of distinct generated forms, in a fixed order (a "-s"/"-es" form,
        then a "-ed"/"-d" form, then an "-ing" form), skipping any that would
        duplicate an earlier one.
    """
    if not headword:
        return ()

    lowered = headword.lower()
    ends_in_y = _ends_in_y_after_consonant(lowered)
    ends_in_e = lowered.endswith("e") and not lowered.endswith("ee")
    doubles = _is_cvc(lowered)

    candidates = (
        _plural_like(headword, lowered, ends_in_y=ends_in_y),
        _past_like(headword, ends_in_y=ends_in_y, ends_in_e=ends_in_e, doubles=doubles),
        _ing_form(headword, ends_in_e=ends_in_e, doubles=doubles),
    )
    forms: list[str] = []
    for form in candidates:
        if form not in forms:
            forms.append(form)
    return tuple(forms)
