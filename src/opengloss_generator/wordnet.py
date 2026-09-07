"""WordNet as an *optional* free signal (D-8, D-79).

Every other free filter in this project is computed from the store itself. This one is
computed from an outside lexicon, which makes it the first check the project has that can be
*absent*: ``nltk`` is an optional dependency (``pip install 'opengloss-generator[wordnet]'``)
and its WordNet corpus is a separate download (``python -m nltk.downloader wordnet``). So every
function here answers ``None`` for "I could not look this up", which is deliberately *not*
``False`` — a caller must not read a missing corpus as evidence that a word is absent from
WordNet. :func:`availability` says which of the two it is, once, so a pass can count the skip
and say so in its own summary rather than silently changing its verdicts.

What the pass actually asks
---------------------------

``inflection_fold`` asks whether a surface form that the store also holds as an inflection of
another lexeme — "glasses" beside "glass", "databases" beside "database" — is a lexeme in its
own right. WordNet answers that better than any rule over the store can, because WordNet's
lemma index is a lemma index: "glasses" is in it (the spectacles sense) and "databases" is not.

The distinction that matters is between a synset WordNet lists the form under *as a lemma* and
a synset ``nltk`` reaches by lemmatising the form first. ``wn.synsets("databases")`` is **not**
empty: NLTK runs ``morphy`` inside ``synsets()``, so it returns *database*'s synset. Asking
that question directly would keep every plural in the store. :func:`lemma_synsets` therefore
filters ``wn.synsets()`` down to the synsets that actually name the form among their own
lemmas, which is the WordNet-as-lemma question:

    >>> lemma_synsets("databases")            # doctest: +SKIP
    frozenset()
    >>> lemma_synsets("glasses")              # doctest: +SKIP
    frozenset({'spectacles.n.01'})

:func:`distinct_from_lemma` then subtracts the base lemma's own synsets, because a form can be
a WordNet lemma *of the base word's own synset* — WordNet lists "arms" under ``weaponry.n.01``,
which "arm" is not in, and that is the keep; it lists "customs" only under synsets "custom" is
also in, which is not. Both directions are what the caller needs and neither is a judgement on
its own: a ``False`` here is the *absence* of free evidence for keeping, never evidence for
folding, and the caller buys a model verdict for it.

Multiword lemmas are stored with underscores (``on_top_of``), so :func:`normalise` is applied
to everything on the way in and every lemma name compared on the way out; that is what lets
``fragments`` ask whether "of course" is a phrase WordNet knows before it retires it. That
coverage is partial and the caller treats it as such: "on top of" and "in front of" are not
WordNet lemmas, so a ``False`` here is the absence of free evidence, never evidence of a
fragment.

Cost and caching
----------------

Nothing here makes a network call and nothing writes. The corpus loads lazily on first use
(about 10 MB, a second or so) and every lookup is memoised per process, so a sweep that asks
about the same lemma once per candidate pays for it once.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from opengloss_generator.log import get_logger

__all__ = [
    "Availability",
    "as_lemma",
    "availability",
    "distinct_from_lemma",
    "evidence_line",
    "lemma_synsets",
    "normalise",
    "reset_cache",
]

_LOG = get_logger(__name__)

#: How many distinct lookups one process keeps. A sweep over the whole store asks about at most
#: two strings per candidate (the form and its lemma) and the candidate list is tens of
#: thousands long, so this holds the working set of a full run without bounding it by memory.
_CACHE_SIZE: Final = 200_000


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether WordNet can be consulted in this process, and why not when it cannot.

    Attributes:
        usable: Whether every function in this module returns real answers rather than ``None``.
        reason: ``None`` when usable; otherwise a short, stable string for a run summary —
            ``"nltk not installed"`` or ``"wordnet corpus not downloaded"``.
    """

    usable: bool
    reason: str | None = None


def normalise(phrase: str) -> str:
    """Return the WordNet lemma key for a headword or surface form.

    WordNet stores a multiword lemma with underscores and every lemma lower-cased, so this is
    the one place a project string is turned into a WordNet one.

    Args:
        phrase: A headword, an inflected form, or a multiword expression.

    Returns:
        The lower-cased, underscore-joined key. Runs of whitespace collapse to one underscore,
        so ``"on  top of"`` and ``"on top of"`` are the same key.
    """
    return "_".join(phrase.lower().split())


@lru_cache(maxsize=1)
def _corpus() -> Any | None:  # noqa: ANN401 - nltk ships no type stubs
    """Return the loaded WordNet reader, or ``None`` when it cannot be loaded.

    Cached at size 1 so the import cost and the corpus load happen once per process, and so a
    missing corpus is reported once rather than on every candidate.

    Returns:
        NLTK's ``wordnet`` corpus reader, already forced to load, or ``None``.
    """
    try:
        from nltk.corpus import wordnet  # noqa: PLC0415 - optional dependency, imported lazily
    except ImportError:
        _LOG.info("wordnet_unavailable", reason="nltk not installed")
        return None
    try:
        # `LazyCorpusLoader` defers everything to first attribute use, so a corpus that is not
        # downloaded raises here rather than at the first real question — which is exactly
        # where a caller wants to learn it.
        wordnet.ensure_loaded()
    except LookupError:
        _LOG.info("wordnet_unavailable", reason="wordnet corpus not downloaded")
        return None
    return wordnet


def availability() -> Availability:
    """Return whether WordNet can be consulted, loading the corpus on first call.

    Returns:
        An :class:`Availability`. A pass calls this once, before its sweep, so its summary can
        report the skip instead of quietly answering every WordNet question ``None``.
    """
    reader = _corpus()
    if reader is not None:
        return Availability(usable=True)
    try:
        import nltk  # noqa: F401, PLC0415 - presence check only
    except ImportError:
        return Availability(usable=False, reason="nltk not installed")
    return Availability(usable=False, reason="wordnet corpus not downloaded")


def reset_cache() -> None:
    """Forget the loaded corpus and every memoised lookup.

    For tests that simulate WordNet's absence, and for nothing else: the corpus is immutable
    and a long-lived process has no reason to drop it.
    """
    _corpus.cache_clear()
    _lemma_synsets.cache_clear()


@lru_cache(maxsize=_CACHE_SIZE)
def _lemma_synsets(key: str) -> frozenset[str] | None:
    """Return the names of every synset that lists ``key`` among its own lemmas.

    Args:
        key: A lemma key already passed through :func:`normalise`.

    Returns:
        Synset names, or ``None`` when WordNet is unavailable.
    """
    reader = _corpus()
    if reader is None:
        return None
    names: set[str] = set()
    # `synsets()` lemmatises internally, so its result is a superset: it answers "what could
    # this string be about", and the question here is the narrower "what is this string a
    # lemma of". Filtering on the synsets' own lemma names is what makes it the narrow one.
    for synset in reader.synsets(key):
        if any(lemma.name().lower() == key for lemma in synset.lemmas()):
            names.add(synset.name())
    return frozenset(names)


def lemma_synsets(phrase: str) -> frozenset[str] | None:
    """Return the synsets WordNet lists ``phrase`` under *as a lemma*.

    Args:
        phrase: A headword, inflected form or multiword expression, in project spelling.

    Returns:
        The synset names, empty when WordNet knows the string but not as a lemma of anything,
        or ``None`` when WordNet is unavailable.
    """
    return _lemma_synsets(normalise(phrase))


def as_lemma(phrase: str) -> bool | None:
    """Return whether WordNet holds ``phrase`` as a lemma of at least one synset.

    The question ``fragments`` asks before it retires a multiword headword: "of course" and
    "out of stock" are WordNet lemmas, so they are lexical units whatever their first and last
    tokens are, while "some sugar" is not in WordNet at all. WordNet's multiword coverage is
    partial ("on top of" is missing), so ``False`` means only that this check found no reason
    to keep the phrase.

    Args:
        phrase: The headword to look up.

    Returns:
        ``True``/``False``, or ``None`` when WordNet is unavailable — which a caller must not
        collapse into ``False``.
    """
    synsets = lemma_synsets(phrase)
    return None if synsets is None else bool(synsets)


def distinct_from_lemma(form: str, lemma: str) -> bool | None:
    """Return whether ``form`` is a WordNet lemma in its own right, beyond ``lemma``'s senses.

    True exactly when WordNet lists ``form`` as the lemma of at least one synset that it does
    *not* list ``lemma`` under. That is the shape of the words this signal exists to protect:
    "arms" is a lemma of ``weaponry.n.01`` and ``coat_of_arms.n.01``, neither of which "arm"
    is a lemma of, so the plural entry is not a plural entry. "customs" is a lemma only of
    synsets "custom" is also a lemma of, so WordNet offers nothing for keeping it and the
    caller buys a verdict instead.

    Args:
        form: The candidate surface form — the headword being judged.
        lemma: The headword whose morphology records ``form`` as an inflection.

    Returns:
        ``True``/``False``, or ``None`` when WordNet is unavailable.
    """
    form_synsets = lemma_synsets(form)
    if form_synsets is None:
        return None
    base_synsets = lemma_synsets(lemma) or frozenset()
    return bool(form_synsets - base_synsets)


def evidence_line(form: str, lemma: str) -> str:
    """Return one line of WordNet evidence to put in a model prompt.

    Stated as fact rather than as a recommendation: the model is being asked for an
    independent judgement, and a prompt that says "WordNet suggests keeping this" would be
    asking it to agree rather than to decide.

    Args:
        form: The candidate surface form.
        lemma: The headword it is recorded as an inflection of.

    Returns:
        A single line, safe to interpolate into a prompt even when WordNet is unavailable.
    """
    own = lemma_synsets(form)
    if own is None:
        return "WordNet: not consulted (corpus unavailable)."
    base = lemma_synsets(lemma) or frozenset()
    if not own:
        return f"WordNet: {form!r} is not a lemma in WordNet."
    extra = sorted(own - base)
    shared = sorted(own & base)
    parts = [f"WordNet: {form!r} is a lemma of {len(own)} synset(s)"]
    if extra:
        parts.append(f"{len(extra)} of them not shared with {lemma!r}: {', '.join(extra[:5])}")
    if shared and not extra:
        parts.append(f"all of them also listed under {lemma!r}: {', '.join(shared[:5])}")
    return "; ".join(parts) + "."
