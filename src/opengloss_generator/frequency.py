"""Zipf frequency scaling, with no dependencies.

A raw corpus count is not comparable across corpora of different sizes, and it is not
what a curriculum designer reasons in — "37,412 occurrences" says nothing on its own. The
Zipf scale (van Heuven, Mandera, Keuleers & Brysbaert, 2014, "SUBTLEX-UK") fixes both
problems: it is a log frequency per billion words, so it is corpus-size-independent and
falls in a small, memorable range.

The formula, per van Heuven et al. (2014)::

    zipf = log10((count + 1) / corpus_tokens * 1e9)

The ``+ 1`` is a Laplace (add-one) smoothing term: it keeps the argument to ``log10``
strictly positive when ``count`` is ``0``, so an unattested word gets a finite (very low,
strongly negative-leaning-toward-1) Zipf value instead of raising on ``log10(0)``.

Interpretation bands (van Heuven et al., 2014, and the SUBTLEX family of papers that
adopted the scale): Zipf values run roughly 1-7 for real corpora.

* **1-3**: low-frequency words.
* **~3**: about one occurrence per million words — a natural low/high boundary.
* **4-7**: high-frequency words, with 7 near the frequency of the single most common
  word in a large corpus ("the").
"""

from __future__ import annotations

import math

__all__ = ["zipf_scale"]


def zipf_scale(count: int, corpus_tokens: int) -> float:
    """Return the Zipf frequency for a raw corpus count.

    Args:
        count: The raw occurrence count of the word in the corpus. Must be non-negative.
        corpus_tokens: The total token count of the corpus the count was drawn from.
            Must be positive.

    Returns:
        The Zipf-scale frequency, ``log10((count + 1) / corpus_tokens * 1e9)``. See the
        module docstring for the interpretation bands.

    Raises:
        ValueError: If ``count`` is negative or ``corpus_tokens`` is not positive.
    """
    if count < 0:
        raise ValueError(f"count must be non-negative, got {count}")
    if corpus_tokens <= 0:
        raise ValueError(f"corpus_tokens must be positive, got {corpus_tokens}")
    return math.log10((count + 1) / corpus_tokens * 1e9)
