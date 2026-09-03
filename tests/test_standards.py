"""Acceptance criterion 1 of ``docs/STANDARDS-PLAN.md`` § 6.

"Every enum member of ``PartOfSpeech``, ``RelationType``, ``EntityType``, ``Register``,
``ReadingLevel`` has a documented external mapping or an explicit ``og`` namespace
marker; a test enumerates each enum and asserts this." One test module, one test per
enum, so a future enum member that nobody gave a standards mapping fails loudly here
instead of silently shipping undocumented.
"""

from __future__ import annotations

from opengloss_generator.schema import (
    LEXINFO_MAP,
    ONTONOTES_MAP,
    READING_LEVEL_CROSSWALK,
    SCHEMA_ORG_MAP,
    TBX_REGISTER_MAP,
    UPOS_MAP,
    WN_RELATION_MAP,
    EntityType,
    PartOfSpeech,
    ReadingLevel,
    Register,
    RelationType,
)


def test_part_of_speech_members_all_have_a_upos_and_lexinfo_mapping():
    for member in PartOfSpeech:
        assert member in UPOS_MAP, f"{member} has no UPOS_MAP entry"
        assert member in LEXINFO_MAP, f"{member} has no LEXINFO_MAP entry"


def test_relation_type_members_are_wn_mapped_or_explicitly_og_namespaced():
    for member in RelationType:
        if member.namespace == "og":
            continue  # explicit og namespace marker: confusable_with, used_with, collocation
        assert member.namespace == "wn"
        assert member in WN_RELATION_MAP, f"{member} is wn-namespaced but not in WN_RELATION_MAP"


def test_entity_type_members_all_have_an_ontonotes_or_schema_org_mapping():
    for member in EntityType:
        # A member may have no OntoNotes analogue (SPECIES, OTHER) -- that gap is
        # itself documented as `None` in ONTONOTES_MAP, which is what "has a mapping"
        # means here: the question was asked and answered, not skipped.
        assert member in ONTONOTES_MAP, f"{member} has no ONTONOTES_MAP entry"
        assert member in SCHEMA_ORG_MAP, f"{member} has no SCHEMA_ORG_MAP entry"


def test_register_members_all_have_a_tbx_mapping_or_are_documented_as_unmapped():
    for member in Register:
        # FORMAL and MARKETING are documented `None`s (no DC-423 analogue) -- still an
        # explicit, asked-and-answered entry, not a silent omission.
        assert member in TBX_REGISTER_MAP, f"{member} has no TBX_REGISTER_MAP entry"


def test_reading_level_members_all_have_a_crosswalk():
    for member in ReadingLevel:
        assert member in READING_LEVEL_CROSSWALK, f"{member} has no READING_LEVEL_CROSSWALK entry"
