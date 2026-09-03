"""Deterministic span finding must be pure, whole-word, and offset-exact."""

from __future__ import annotations

import pytest

from opengloss_generator.spans import find_span, generate_forms, unresolved


@pytest.mark.parametrize(
    ("text", "headword", "forms", "expected_text"),
    [
        # Exact match.
        ("She learned to abseil last summer.", "abseil", (), "abseil"),
        # Case-insensitive match; offsets must index the original (non-lowered) text.
        ("Abseil is a useful skill.", "abseil", (), "Abseil"),
        ("SHE WILL ABSEIL DOWN.", "abseil", (), "ABSEIL"),
        # Match via a supplied inflected form.
        ("She abseiled off the cliff.", "abseil", ("abseiled",), "abseiled"),
        ("She was abseiling all day.", "abseil", ("abseiling",), "abseiling"),
        # Possessive: apostrophe does not block the whole-word boundary.
        ("The abseil's anchor point failed.", "abseil", (), "abseil"),
        # Punctuation adjacency.
        ("Gear needed: (abseil) rope and harness.", "abseil", (), "abseil"),
        # Headword at the very start of the text.
        ("Abseil down carefully.", "abseil", (), "Abseil"),
        # Headword at the very end of the text.
        ("Let's go abseil", "abseil", (), "abseil"),
        # Multi-word headword, exact separator.
        ("Bring an ice axe on the glacier.", "ice axe", (), "ice axe"),
        # Multi-word headword, hyphen variant in text.
        ("Bring an ice-axe on the glacier.", "ice axe", (), "ice-axe"),
        # Multi-word headword, extra internal whitespace and mixed case in text.
        ("Bring an Ice  Axe on the glacier.", "ice axe", (), "Ice  Axe"),
        # Multi-word headword, underscore variant in text.
        ("Bring an ice_axe on the glacier.", "ice axe", (), "ice_axe"),
        # Longest-wins: a longer form beats the bare headword even though the
        # headword occurs earlier in the text.
        (
            "Pack the ice axe first, then the spare ice axes for the team.",
            "ice axe",
            ("ice axes",),
            "ice axes",
        ),
        # Earliest-wins on a length tie between two occurrences of the same term.
        ("The cat sat, then the cat slept.", "cat", (), "cat"),
    ],
)
def test_find_span_positive(text, headword, forms, expected_text):
    span = find_span(text, headword, forms)
    assert span is not None
    start, end = span
    assert 0 <= start < end <= len(text)
    assert text[start:end] == expected_text


def test_find_span_earliest_wins_returns_first_occurrence_offset():
    text = "The cat sat, then the cat slept."
    start, end = find_span(text, "cat")
    assert (start, end) == (4, 7)
    assert text[start:end] == "cat"


@pytest.mark.parametrize(
    ("text", "headword", "forms"),
    [
        # "abseiling" must not match headword "abseil" unless supplied as a form.
        ("She was abseiling all day.", "abseil", ()),
        # No occurrence at all.
        ("There is nothing relevant here.", "abseil", ()),
        # Empty text.
        ("", "abseil", ()),
    ],
)
def test_find_span_negative(text, headword, forms):
    assert find_span(text, headword, forms) is None


def test_find_span_unicode_casefold_length_differs():
    # "straße".casefold() == "strasse" (7 chars, one longer than "straße"'s 6),
    # so an implementation that searched a casefolded copy and reused the match
    # length against the original text would slice one character too many.
    text = "Wir gingen die Straße entlang."
    span = find_span(text, "straße")
    assert span is not None
    start, end = span
    matched = text[start:end]
    assert matched == "Straße"
    assert matched.casefold() == "straße".casefold()
    assert end - start == len("straße")


def test_unresolved_reports_only_failed_indices():
    examples = [
        ("She learned to abseil last summer.", "abseil", ()),
        ("There is nothing relevant here.", "abseil", ()),
        ("She was abseiling all day.", "abseil", ("abseiling",)),
        ("Totally unrelated sentence.", "ice axe", ()),
    ]
    assert unresolved(examples) == [1, 3]


@pytest.mark.parametrize(
    ("headword", "expected"),
    [
        ("run", ("runs", "runned", "running")),
        ("carry", ("carries", "carried", "carrying")),
        ("make", ("makes", "maked", "making")),
        ("box", ("boxes", "boxed", "boxing")),
        ("abseil", ("abseils", "abseiled", "abseiling")),
    ],
)
def test_generate_forms(headword, expected):
    assert generate_forms(headword) == expected


@pytest.mark.parametrize("headword", ["run", "carry", "make", "box", "abseil"])
def test_generate_forms_regular_forms_are_findable(headword):
    # Whatever generate_forms produces should itself be usable as a `forms`
    # candidate for find_span -- the point of the heuristic.
    forms = generate_forms(headword)
    for form in forms:
        text = f"Example sentence containing {form} somewhere in the middle."
        span = find_span(text, headword, forms)
        assert span is not None
        start, end = span
        assert text[start:end].lower() == form.lower()


def test_generate_forms_empty_headword():
    assert generate_forms("") == ()
