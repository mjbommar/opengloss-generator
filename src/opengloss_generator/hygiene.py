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

A second, unrelated defect gets the same treatment (D-59, F7): a register rendition of a
gloss — the ``informal``, ``technical``, ``formal``, ``slang``, ``in_house`` or
``marketing`` rewrite of the same one-sentence definition — is supposed to say the same
thing in noticeably different words. A model asked for ten of these at once cheaply
satisfies the letter of the request by swapping two words and calling it ``technical``,
which is a rewrite in name only. :func:`lexical_diversity` measures how much two texts'
*wording* actually differs, and :func:`is_near_copy` is the yes/no verdict built on top of
it: a register rendition scoring at or above 0.9 Jaccard similarity against the canonical
gloss it was rewritten from (0.1 or less lexical diversity) has not done its job, whatever
its content is otherwise like. The same three call sites apply it as apply
:func:`is_headword_initial`:

* ``workflows/enrich.py`` measures it on every non-``plain`` gloss rendition as it is
  generated, treats a hit as a miss sharing the one retry, and flags what survives with
  :data:`~opengloss_generator.schema.QAFlag.OG_NEAR_COPY`.
* ``workflows/retrofit.py``'s ``rendition_hygiene`` pass finds the already-stored
  renditions that are near-copies and flags them; unlike the headword-initial defect this
  one is not spent on a rewrite call, because a paraphrase a model was already told to
  write differently is not made better by asking it again in the same words.
* Both are free: a Jaccard score over two short texts' content-word sets costs nothing to
  compute.

Unlike the headword-initial check there is no proper-noun exemption: a proper noun's
formal and slang registers still have to read differently from each other, whatever its
canonical gloss says about the entity it names.
"""

from __future__ import annotations

import re

__all__ = ["content_words", "is_headword_initial", "is_near_copy", "lexical_diversity"]

#: Determiners that do not buy a definition anything: "A ban is an order to stop." is
#: headword-initial in every sense that matters, and so is "The people are human beings."
_ARTICLES = "a|an|the"

#: A small, closed stopword list for :func:`content_words`. Deliberately conservative —
#: articles, pronouns, conjunctions, prepositions and the common forms of "be", "have" and
#: "do" — so a real content word is never dropped by accident; a stopword slipping through
#: only makes two texts look *less* diverse than they are, which is the safe direction for
#: a near-copy check to err in.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "and", "but", "or", "nor", "so", "yet", "for", "not",
        "if", "because", "although", "though", "while", "as", "than", "that",
        "i", "me", "my", "mine", "myself",
        "you", "your", "yours", "yourself",
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "we", "us", "our", "ours", "ourselves",
        "they", "them", "their", "theirs", "themselves",
        "who", "whom", "whose", "which", "what",
        "this", "these", "those",
        "am", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "done",
        "have", "has", "had", "having",
        "can", "could", "shall", "should", "will", "would", "may", "might", "must",
        "at", "by", "from", "in", "into", "of", "on", "to", "up", "with", "within",
        "about", "above", "after", "before", "between", "over", "under", "out",
        "there", "here", "some", "any", "all", "no", "such",
    }
)  # fmt: skip

#: A run of ASCII letters, lowercased before the stopword check. Apostrophes split the
#: token either side ("don't" -> "don", "t"), which the plan's own wording asks for:
#: content words are "lowercase alphabetic tokens", not word-boundary tokens in general.
_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z]+")

#: A register rendition at or above this Jaccard similarity to its canonical gloss is a
#: near-copy (D-59): 1 - 0.9 = 0.1 lexical diversity, the floor of the target band the
#: rendition instructions ask for (0.30-0.60) with a wide margin below it, so ordinary
#: variation between two honest rewrites of a short sentence never trips the check.
NEAR_COPY_JACCARD_THRESHOLD = 0.9


def content_words(text: str) -> set[str]:
    """Return the lowercase alphabetic content words of a text.

    A content word is a maximal run of ASCII letters, lowercased, that is not on the small
    :data:`_STOPWORDS` list. Numbers, punctuation and symbols contribute no tokens.

    Args:
        text: The text to tokenise.

    Returns:
        The set of distinct content words; empty for a text with none.
    """
    return {
        word
        for word in (match.group(0).lower() for match in _ALPHA_TOKEN_RE.finditer(text))
        if word not in _STOPWORDS
    }


def lexical_diversity(a: str, b: str) -> float:
    """Return how much two texts' wording differs: ``1 - Jaccard`` over content words.

    ``0.0`` means the two texts share exactly the same content-word set (including the
    degenerate case where both have none at all, by convention rather than by a division
    that would otherwise be undefined); ``1.0`` means they share none. This is a *wording*
    measure, not a meaning one — two independently written sentences with the same handful
    of nouns score as similar as two sentences that really are the same rewrite, which is
    why the near-copy check this feeds (:func:`is_near_copy`) is a low floor (0.1) rather
    than a target: it catches the model swapping two words and calling it a rewrite, not
    every rewrite that happens to reuse a proper noun.

    Args:
        a: The first text.
        b: The second text.

    Returns:
        A value in ``[0.0, 1.0]``.
    """
    words_a = content_words(a)
    words_b = content_words(b)
    union = words_a | words_b
    if not union:
        return 0.0
    return 1.0 - len(words_a & words_b) / len(union)


def is_near_copy(a: str, b: str, *, threshold: float = NEAR_COPY_JACCARD_THRESHOLD) -> bool:
    """Return whether two texts' content-word sets overlap at or above ``threshold``.

    Args:
        a: The first text (the candidate rewrite).
        b: The second text (the canonical gloss it was rewritten from).
        threshold: The Jaccard similarity at or above which the pair counts as a
            near-copy. Defaults to :data:`NEAR_COPY_JACCARD_THRESHOLD`.

    Returns:
        Whether ``1 - lexical_diversity(a, b) >= threshold``.
    """
    return 1.0 - lexical_diversity(a, b) >= threshold


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
