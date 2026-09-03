"""Taxonomy invariants: root coverage, leaf balance, prompt-block stability, legacy mapping."""

from __future__ import annotations

import importlib
from collections import Counter

from opengloss_generator import taxonomy as taxonomy_module
from opengloss_generator.taxonomy import (
    GLOSSES,
    IPTC_MAP,
    LCC_MAP,
    LEAF_COUNT,
    LEGACY_DOMAIN_MAP,
    ROOTS,
    TAXONOMY_PROMPT_BLOCK,
    TAXONOMY_VERSION,
    DomainTag,
    deficit_table,
    is_general,
    leaves_of,
    legacy_domain,
    root_of,
)

#: Roots whose finer-grained clusters (D-44: personal names/character traits/social
#: roles/emotion in ``people_society``; actions-routines/quantity-time in
#: ``everyday_life``) push them past the 8-12 band every other root stays within.
_ROOTS_WITH_RAISED_LEAF_CAP = frozenset({"everyday_life", "people_society"})

REQUIRED_LEGACY_STRINGS = [
    "general academic",
    "history",
    "geography",
    "art",
    "civics",
    "biology",
    "physics",
    "chemistry",
    "mathematics",
    "literature",
    "music",
    "technology",
    "medicine",
    "law",
    "economics",
    "sports",
    "religion",
    "philosophy",
    "psychology",
    "computing",
]


def test_roots_are_the_fixed_fifteen():
    assert ROOTS == (
        "arts",
        "business",
        "education",
        "everyday_life",
        "health",
        "history",
        "humanities",
        "language",
        "law_government",
        "mathematics",
        "nature",
        "people_society",
        "science",
        "sports_recreation",
        "technology",
    )
    assert len(ROOTS) == 15


def test_every_value_root_is_in_roots():
    for tag in DomainTag:
        assert root_of(tag) in ROOTS


def test_every_root_has_a_general_leaf_and_8_to_14_leaves():
    for root in ROOTS:
        leaves = leaves_of(root)
        cap = 14 if root in _ROOTS_WITH_RAISED_LEAF_CAP else 12
        assert 8 <= len(leaves) <= cap, f"{root} has {len(leaves)} leaves (cap {cap})"
        general_leaves = [tag for tag in leaves if is_general(tag)]
        assert general_leaves == [DomainTag(f"{root}.general")]


def test_only_the_documented_roots_use_the_raised_leaf_cap():
    # D-44: everyday_life and people_society are the only roots carrying 13-14 leaves;
    # every other root stays within the original 8-12 band.
    for root in ROOTS:
        leaves = leaves_of(root)
        if root not in _ROOTS_WITH_RAISED_LEAF_CAP:
            assert len(leaves) <= 12, f"{root} has {len(leaves)} leaves but is not raised-cap"


def test_leaves_of_only_returns_matching_root():
    for root in ROOTS:
        for tag in leaves_of(root):
            assert tag.value.startswith(f"{root}.")


def test_leaves_of_unknown_root_is_empty():
    assert leaves_of("not_a_real_root") == ()


def test_total_leaf_count_is_in_target_band():
    assert len(DomainTag) == LEAF_COUNT
    assert 130 <= LEAF_COUNT <= 180


def test_taxonomy_version_is_a_nonempty_string():
    assert isinstance(TAXONOMY_VERSION, str)
    assert TAXONOMY_VERSION != ""


def test_values_are_unique_lowercase_root_dot_leaf():
    values = [tag.value for tag in DomainTag]
    assert len(values) == len(set(values))
    for value in values:
        assert value == value.lower()
        parts = value.split(".")
        assert len(parts) == 2
        root, leaf = parts
        assert root
        assert leaf
        assert root in ROOTS


def test_is_general_matches_dot_general_suffix():
    for tag in DomainTag:
        assert is_general(tag) == tag.value.endswith(".general")
    assert is_general(DomainTag.SCIENCE_GENERAL)
    assert not is_general(DomainTag.SCIENCE_PHYSICS)


def test_prompt_block_is_byte_stable_across_calls_and_imports():
    first = TAXONOMY_PROMPT_BLOCK
    second = taxonomy_module.TAXONOMY_PROMPT_BLOCK
    assert first == second

    reloaded = importlib.reload(taxonomy_module)
    assert first == reloaded.TAXONOMY_PROMPT_BLOCK


def test_prompt_block_contains_every_leaf_exactly_once():
    for tag in DomainTag:
        needle = f"{tag.value} — {GLOSSES[tag]}"
        assert TAXONOMY_PROMPT_BLOCK.count(needle) == 1


def test_prompt_block_has_a_gloss_for_every_member():
    assert set(GLOSSES.keys()) == set(DomainTag)


def test_prompt_block_groups_leaves_under_root_headings():
    for root in ROOTS:
        assert f"# {root}" in TAXONOMY_PROMPT_BLOCK


def test_legacy_map_covers_the_required_strings():
    for text in REQUIRED_LEGACY_STRINGS:
        assert text in LEGACY_DOMAIN_MAP
        assert isinstance(LEGACY_DOMAIN_MAP[text], DomainTag)


def test_legacy_map_general_academic_maps_to_a_general_leaf():
    tag = LEGACY_DOMAIN_MAP["general academic"]
    assert is_general(tag)
    assert tag == DomainTag.EDUCATION_GENERAL


def test_legacy_domain_is_case_and_whitespace_insensitive():
    for text in REQUIRED_LEGACY_STRINGS:
        expected = LEGACY_DOMAIN_MAP[text]
        assert legacy_domain(text) == expected
        assert legacy_domain(text.upper()) == expected
        assert legacy_domain(f"  {text}  ") == expected
        assert legacy_domain(" ".join(text.split(" "))) == expected


def test_legacy_domain_unknown_string_returns_none():
    assert legacy_domain("not a real legacy domain") is None
    assert legacy_domain("") is None


def test_deficit_table_sums_to_approximately_zero():
    counts = Counter(
        {
            DomainTag.SCIENCE_PHYSICS: 10,
            DomainTag.ARTS_MUSIC: 5,
            DomainTag.HISTORY_GENERAL: 3,
        }
    )
    table = deficit_table(counts)
    assert set(table) == set(ROOTS)
    assert abs(sum(table.values())) < 1e-9


def test_deficit_table_ranks_an_empty_root_as_most_deficient():
    counts = Counter(
        {
            DomainTag.SCIENCE_PHYSICS: 50,
            DomainTag.ARTS_MUSIC: 30,
            DomainTag.HISTORY_GENERAL: 20,
        }
    )
    # every other root, including "technology", has zero occurrences in `counts`.
    table = deficit_table(counts)
    most_deficient = max(table, key=lambda root: table[root])
    assert table["technology"] == max(table.values())
    assert most_deficient in {root for root in ROOTS if root not in {"science", "arts", "history"}}


def test_deficit_table_with_no_counts_uses_uniform_target_and_full_deficit():
    table = deficit_table({})
    uniform = 1.0 / len(ROOTS)
    for root in ROOTS:
        assert table[root] == uniform


def test_deficit_table_custom_target_share():
    counts = Counter({DomainTag.SCIENCE_PHYSICS: 1})
    target = dict.fromkeys(ROOTS, 0.0)
    target["science"] = 1.0
    table = deficit_table(counts, target_share=target)
    assert table["science"] == 0.0
    assert table["arts"] == 0.0


# --------------------------------------------------------------------------------------
# C1 -- LCC / IPTC crosswalk (docs/STANDARDS-PLAN.md § 4)
# --------------------------------------------------------------------------------------


def test_lcc_map_covers_every_root_with_letters_in_a_through_z():
    assert set(LCC_MAP) == set(ROOTS)
    for root, letters in LCC_MAP.items():
        assert letters, f"{root} has no LCC class at all"
        for letter in letters:
            assert letter.isalpha()
            assert letter.isupper()
            assert "A" <= letter <= "Z"


def test_iptc_map_covers_every_root():
    assert set(IPTC_MAP) == set(ROOTS)
    for codes in IPTC_MAP.values():
        for code in codes:
            assert code.startswith("medtop:")


def test_history_has_no_iptc_top_level_topic_and_that_is_documented_not_missing():
    # IPTC is a news-event taxonomy with no counterpart for "history as a subject"
    # (STANDARDS.md § 5c) -- the key is present (the root is covered) but the tuple of
    # codes is deliberately empty, not silently absent from IPTC_MAP.
    assert "history" in IPTC_MAP
    assert IPTC_MAP["history"] == ()
