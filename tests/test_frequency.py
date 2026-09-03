"""The Zipf frequency scaling formula.

``zipf_scale`` is a pure function of two numbers, so its tests are exact: hand-computed
values from the van Heuven et al. (2014) formula, not approximations of them.
"""

from __future__ import annotations

import math

import pytest

from opengloss_generator.frequency import zipf_scale


def test_zipf_scale_matches_the_worked_example():
    # (1000 + 1) / 1e9 * 1e9 = 1001; log10(1001) ~= 3.0004 -- "about once per million".
    assert zipf_scale(1000, 1_000_000_000) == pytest.approx(3.0, abs=1e-3)


def test_zipf_scale_matches_the_formula_directly():
    count, corpus_tokens = 37_412, 987_654_321
    expected = math.log10((count + 1) / corpus_tokens * 1e9)
    assert zipf_scale(count, corpus_tokens) == pytest.approx(expected)


def test_zipf_scale_of_zero_count_is_finite_via_laplace_smoothing():
    # Without the +1 term this would be log10(0), a domain error.
    value = zipf_scale(0, 1_000_000_000)
    assert math.isfinite(value)
    assert value < 3.0


def test_zipf_scale_rejects_negative_count():
    with pytest.raises(ValueError, match="non-negative"):
        zipf_scale(-1, 1_000_000_000)


@pytest.mark.parametrize("corpus_tokens", [0, -1])
def test_zipf_scale_rejects_non_positive_corpus_tokens(corpus_tokens):
    with pytest.raises(ValueError, match="positive"):
        zipf_scale(1000, corpus_tokens)
