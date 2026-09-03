"""Identifier derivation must be pure, stable, and reproducible from an export."""

from __future__ import annotations

import pytest

from opengloss_generator.identity import (
    edge_id,
    sense_id,
    shard_for,
    slugify,
    variant_id,
)


@pytest.mark.parametrize(
    ("headword", "expected"),
    [
        ("abseil", "abseil"),
        ("3d model", "3d_model"),
        ("Aaron's", "aaron_s"),
        ("café", "cafe"),
        ("  spaced  out  ", "spaced_out"),
        ("MiXeD CaSe", "mixed_case"),
    ],
)
def test_slugify(headword, expected):
    assert slugify(headword) == expected


def test_slugify_is_idempotent():
    assert slugify(slugify("Aaron's")) == slugify("Aaron's")


def test_slugify_rejects_empty():
    with pytest.raises(ValueError, match="empty slug"):
        slugify("!!!")


def test_derived_ids_are_positional():
    assert sense_id("abseil", "verb", 0) == "abseil:verb:0"
    assert variant_id("abseil:verb:0", "grade_5", "plain") == "abseil:verb:0#grade_5/plain"
    assert edge_id("abseil:verb:0", "synonym", "rappel") == "abseil:verb:0-synonym->rappel"


def test_ids_are_reproducible_without_state():
    # This is the property the v1.3 export lost: a consumer with only the headword,
    # part of speech, and position can recompute every id.
    assert sense_id("abseil", "verb", 1) == sense_id("abseil", "verb", 1)


def test_shard_is_stable_and_balanced():
    assert shard_for("abseil") == shard_for("abseil")
    assert len(shard_for("abseil")) == 2
    # Hash sharding, not first-letter sharding: 's' words must not all land together.
    s_words = [f"s{i}" for i in range(200)]
    buckets = {shard_for(w)[0] for w in s_words}
    assert len(buckets) > 50
