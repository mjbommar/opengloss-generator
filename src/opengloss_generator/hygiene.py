"""Deterministic text-hygiene checks shared by the workflows that enforce them.

One defect needs the same answer in three places, so the answer lives here rather than
in any of them. A dictionary definition must not begin by naming its own headword — "A
ban is an order to stop." tells a reader who already knows the word nothing, and a
reader who does not is sent in a circle. The rule is stated in
:data:`~opengloss_generator.prompts.SENSES_INSTRUCTIONS` and again in
:data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS`, and stating it is not
enough: measured over 400 swept core entries (``docs/CORE-DIARY.md`` iteration 4),
canonical glosses offend at 2.7% but their *renditions* at 10-15% at every non-canonical
target, because the short-sentence targets pull the model towards "X is …".

So the same predicate is applied at three points, and :func:`is_headword_initial` is the
one it is applied with:

* ``workflows/enrich.py`` measures it on every gloss rendition as it is generated, treats
  a hit as a miss exactly like a readability miss (one combined retry), and flags what
  survives with :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_INITIAL`.
* ``workflows/retrofit.py`` uses it twice: the ``hygiene`` pass's step (c) finds the
  canonical glosses to rewrite, and the ``rendition_hygiene`` pass finds the already-stored
  renditions to rewrite.
* ``audit.py`` counts hits, read-only, so an iteration can report a before and an after.

Proper nouns are exempt (D-30): a proper noun's definition legitimately names its own
entity, as WordNet's do. The exemption is the *caller's* to apply — this module sees one
text and one headword and has no entry to ask about ``kind``.
"""

from __future__ import annotations

import re

__all__ = ["is_headword_initial"]

#: Determiners that do not buy a definition anything: "A ban is an order to stop." is
#: headword-initial in every sense that matters, and so is "The people are human beings."
_ARTICLES = "a|an|the"


def is_headword_initial(text: str, headword: str) -> bool:
    """Return whether a piece of definition text begins by naming its own headword.

    Case-insensitively, ``text`` offends when it begins with any of:

    * the headword itself, plural ``-s`` allowed — which is what "X is", "X means" and
      "X refers to …" all already are;
    * an article plus the headword ("A ban is …", "The people are …");
    * "the word X" or "the term X";
    * "to X is" or "to X means".

    The headword is matched at a word boundary, so a headword of ``ban`` does not fire on
    a definition beginning "Bananas are …".

    Args:
        text: The definition or rendition text to check, markdown already stripped.
        headword: The entry's surface form. A blank headword never matches.

    Returns:
        Whether the text is headword-initial. Proper-noun entries are exempt from the
        rule (D-30), but that exemption belongs to the caller, which is the only side
        that knows the entry's ``kind``.
    """
    term = headword.strip()
    if not term or not text.strip():
        return False
    word = rf"{re.escape(term)}s?\b"
    pattern = re.compile(
        rf"^\s*(?:"
        rf"the\s+(?:word|term)\s+{word}"
        rf"|to\s+{word}\s+(?:is|means)\b"
        rf"|(?:{_ARTICLES})\s+{word}"
        rf"|{word}"
        rf")",
        re.IGNORECASE,
    )
    return bool(pattern.match(text))
