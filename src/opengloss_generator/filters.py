"""Frontier candidate filtering.

Ordered cheapest-first, and every rejection records which filter rejected it so the
rejection set is auditable. Steps 1-5 are free; only survivors reach the LLM classifier.

This ordering is the whole economics of a graph walk. The v1.3 gap-fill scanned ~2.1M
undefined relation targets; sending all of them to a model would have cost more than the
entries it produced. See ``docs/DESIGN.md`` § 5.3.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from opengloss_generator.identity import slugify

__all__ = ["FilterChain", "FilterOutcome", "RejectReason", "normalise_candidate"]

_WHITESPACE = re.compile(r"\s+")
_STRIPPABLE = ".,;:!?\"'()[]{}"

# Reconstructed roots ("*sneu-"), language-family labels, and gloss leakage are the three
# artifact classes that dominated the v1.3 candidate pool.
_ETYMON = re.compile(r"[*√]|^(?:pie|proto)[- ]", re.IGNORECASE)
_SENTENCE_SHAPED = re.compile(r"[.;]|\b(?:which|that is|refers to|meaning)\b", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[a-z]")

_META_LABELS = frozenset(
    {
        "see also",
        "see",
        "cf",
        "compare",
        "figurative",
        "literal",
        "archaic",
        "obsolete",
        "colloquial",
        "slang",
        "informal",
        "formal",
        "none",
        "n/a",
        "na",
        "unknown",
        "various",
        "etc",
        "other",
        "general",
    }
)

MAX_WORDS = 4
MAX_LENGTH = 40
MIN_LENGTH = 2


class RejectReason(StrEnum):
    """Why a candidate was dropped."""

    EMPTY = "empty"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    TOO_MANY_WORDS = "too_many_words"
    NO_LETTERS = "no_letters"
    ETYMON = "etymon"
    META_LABEL = "meta_label"
    SENTENCE_SHAPED = "sentence_shaped"
    ALREADY_STORED = "already_stored"
    DUPLICATE = "duplicate"
    SELF_REFERENCE = "self_reference"


@dataclass(slots=True)
class FilterOutcome:
    """The result of running a candidate set through the chain."""

    accepted: list[str] = field(default_factory=list)
    rejected: dict[str, RejectReason] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        """Return how many candidates survived."""
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        """Return how many candidates were dropped."""
        return len(self.rejected)

    def reason_counts(self) -> dict[str, int]:
        """Return a histogram of rejection reasons, for the run summary."""
        counts: dict[str, int] = {}
        for reason in self.rejected.values():
            counts[reason.value] = counts.get(reason.value, 0) + 1
        return counts


def normalise_candidate(raw: str) -> str:
    """Return a candidate reduced to comparable form.

    Lowercases, collapses internal whitespace, and strips surrounding punctuation.
    Internal punctuation is kept, because hyphens and apostrophes are part of real
    headwords.

    Args:
        raw: The candidate as harvested from a relation field.

    Returns:
        The normalised surface form, possibly empty.
    """
    return _WHITESPACE.sub(" ", raw.strip().strip(_STRIPPABLE)).strip().lower()


class FilterChain:
    """Applies the free filters to a candidate set.

    Args:
        known_ids: Lexeme ids already present in the store; candidates that slug to one
            of these are not frontier.
        source_id: The id of the entry the candidates came from, so an entry that lists
            itself as its own synonym does not re-enter the queue.
    """

    def __init__(self, known_ids: set[str], source_id: str | None = None) -> None:
        """Store the membership set and the originating entry id."""
        self._known = known_ids
        self._source = source_id

    def run(self, candidates: Iterable[str]) -> FilterOutcome:
        """Filter a candidate set.

        Args:
            candidates: Raw candidate strings.

        Returns:
            A :class:`FilterOutcome` holding survivors and per-candidate reasons.
        """
        outcome = FilterOutcome()
        seen: set[str] = set()
        for raw in candidates:
            term = normalise_candidate(raw)
            reason = self._structural_reason(term)
            if reason is None:
                try:
                    slug = slugify(term)
                except ValueError:
                    reason = RejectReason.NO_LETTERS
                else:
                    reason = self._membership_reason(slug, seen)
                    if reason is None:
                        seen.add(slug)
                        outcome.accepted.append(term)
                        continue
            outcome.rejected[raw] = reason
        return outcome

    def _structural_reason(self, term: str) -> RejectReason | None:
        """Return why a normalised term is structurally unusable, or ``None``.

        Checks run in cost order, cheapest first; the first match wins.
        """
        checks: tuple[tuple[bool, RejectReason], ...] = (
            (not term, RejectReason.EMPTY),
            (len(term) < MIN_LENGTH, RejectReason.TOO_SHORT),
            (len(term) > MAX_LENGTH, RejectReason.TOO_LONG),
            (not _HAS_LETTER.search(term), RejectReason.NO_LETTERS),
            (bool(_ETYMON.search(term)), RejectReason.ETYMON),
            (term in _META_LABELS, RejectReason.META_LABEL),
            (bool(_SENTENCE_SHAPED.search(term)), RejectReason.SENTENCE_SHAPED),
            (len(term.split(" ")) > MAX_WORDS, RejectReason.TOO_MANY_WORDS),
        )
        return next((reason for failed, reason in checks if failed), None)

    def _membership_reason(self, slug: str, seen: set[str]) -> RejectReason | None:
        """Return why a slug is not frontier, or ``None`` if it is."""
        if self._source is not None and slug == self._source:
            return RejectReason.SELF_REFERENCE
        if slug in seen:
            return RejectReason.DUPLICATE
        if slug in self._known:
            return RejectReason.ALREADY_STORED
        return None
