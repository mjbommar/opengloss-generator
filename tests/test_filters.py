"""The free filters carry the economics of a walk; they must reject the right things."""

from __future__ import annotations

import pytest

from opengloss_generator.filters import FilterChain, RejectReason, normalise_candidate


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Rappel.  ", "rappel"),
        ("Ice   Axe", "ice axe"),
        ("(descend)", "descend"),
    ],
)
def test_normalisation(raw, expected):
    assert normalise_candidate(raw) == expected


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("", RejectReason.EMPTY),
        ("a", RejectReason.TOO_SHORT),
        ("x" * 60, RejectReason.TOO_LONG),
        ("123", RejectReason.NO_LETTERS),
        ("*sneu-", RejectReason.ETYMON),
        ("PIE root", RejectReason.ETYMON),
        ("see also", RejectReason.META_LABEL),
        ("a rope which is used for descent", RejectReason.SENTENCE_SHAPED),
        ("one two three four five", RejectReason.TOO_MANY_WORDS),
    ],
)
def test_structural_rejections(candidate, reason):
    outcome = FilterChain(known_ids=set()).run([candidate])
    assert outcome.accepted == []
    assert outcome.rejected[candidate] is reason


def test_membership_and_dedup():
    chain = FilterChain(known_ids={"rappel"}, source_id="abseil")
    outcome = chain.run(["rappel", "abseil", "descend", "Descend", "belay"])
    assert outcome.accepted == ["descend", "belay"]
    assert outcome.rejected["rappel"] is RejectReason.ALREADY_STORED
    assert outcome.rejected["abseil"] is RejectReason.SELF_REFERENCE
    assert outcome.rejected["Descend"] is RejectReason.DUPLICATE


def test_reason_histogram_is_auditable():
    outcome = FilterChain(known_ids=set()).run(["*root", "*other", "see also"])
    assert outcome.reason_counts() == {"etymon": 2, "meta_label": 1}
    assert outcome.rejected_count == 3
    assert outcome.accepted_count == 0
