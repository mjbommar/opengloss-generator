"""The OpenGloss entry schema, version 3.0.

Design notes that are not obvious from the field list:

* Identifiers are derived, never stored as random values (``identity.py``).
* Every text-bearing field is a :class:`Renditions` set rather than a bare string. The
  canonical rendition is ``(neutral, plain)``; reading-level and register expansion is
  an *enrichment* of that set, not a separate dataset.
* Sense relations are one typed list of :class:`Relation`, each pointing at a
  :class:`RelationTarget` that starts life as a surface form and is later *resolved* to
  a sense id. Resolution never changes an edge id, so ids are stable across it.
* Semantic edges are *derived* from sense relations via :meth:`Lexeme.edges`; they are
  never stored, so the two cannot disagree.
* Models forbid extra fields, so provider drift — and a v2 payload — surfaces as a
  validation error rather than silent data loss. ``migrate.py`` upgrades older shapes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from opengloss_generator.frequency import zipf_scale
from opengloss_generator.identity import (
    edge_id,
    encyclopedia_owner_id,
    explanation_owner_id,
    pos_entry_id,
    rendition_id,
    sense_id,
    slugify,
)
from opengloss_generator.taxonomy import DomainTag

__all__ = [
    "CANONICAL_KEY",
    "FK_BANDS",
    "FORMALITY_ORDER",
    "LEXINFO_MAP",
    "ONTONOTES_MAP",
    "READING_LEVEL_CROSSWALK",
    "RECONSTRUCTED_LANGUAGE_CODES",
    "SCHEMA_ORG_MAP",
    "SCHEMA_VERSION",
    "SKOS_RELATION_MAP",
    "TBX_REGISTER_MAP",
    "UPOS_MAP",
    "WN_RELATION_MAP",
    "Assessment",
    "Edge",
    "EntityType",
    "EntryStatus",
    "Etymology",
    "EtymologySegment",
    "Example",
    "LevelCrosswalk",
    "Lexeme",
    "LexemeKind",
    "Morphology",
    "POSEntry",
    "PartOfSpeech",
    "ProperNounInfo",
    "Provenance",
    "QAFlag",
    "ReadingLevel",
    "Register",
    "Relation",
    "RelationTarget",
    "RelationType",
    "Rendition",
    "Renditions",
    "Sense",
    "StageName",
    "canonical_rendition",
    "project_concept_id",
    "upos_for",
]

SCHEMA_VERSION = "3.0"


class PartOfSpeech(StrEnum):
    """Part-of-speech tags used across the lexicon."""

    NOUN = "noun"
    VERB = "verb"
    ADJECTIVE = "adjective"
    ADVERB = "adverb"
    PRONOUN = "pronoun"
    PREPOSITION = "preposition"
    CONJUNCTION = "conjunction"
    DETERMINER = "determiner"
    INTERJECTION = "interjection"
    NUMERAL = "numeral"

    @property
    def upos(self) -> str:
        """Return the Universal Dependencies UPOS v2 tag for this part of speech."""
        return UPOS_MAP[self]


#: Universal Dependencies UPOS v2 tag per :class:`PartOfSpeech` member (STANDARDS.md
#: § 1a/1c). ``CONJUNCTION`` is **lossy**: UD splits coordinating (``CCONJ``) from
#: subordinating (``SCONJ``) conjunctions, a distinction our single dictionary-sense
#: value does not carry (a headword's POS entry, unlike a token in a treebank, has no
#: local syntactic context to disambiguate from — see STANDARDS.md § 1d). We collapse to
#: ``CCONJ``, the more common case, and record the collapse here rather than silently
#: picking one. Proper nouns are not a stored POS value at all (``LexemeKind.PROPER_NOUN``
#: plus :class:`ProperNounInfo` carries that instead); :func:`upos_for` applies UD's
#: ``PROPN`` rule at export time without a stored value.
UPOS_MAP: dict[PartOfSpeech, str] = {
    PartOfSpeech.NOUN: "NOUN",
    PartOfSpeech.VERB: "VERB",
    PartOfSpeech.ADJECTIVE: "ADJ",
    PartOfSpeech.ADVERB: "ADV",
    PartOfSpeech.PRONOUN: "PRON",
    PartOfSpeech.PREPOSITION: "ADP",
    PartOfSpeech.CONJUNCTION: "CCONJ",  # lossy: SCONJ collapsed, see docstring above
    PartOfSpeech.DETERMINER: "DET",
    PartOfSpeech.INTERJECTION: "INTJ",
    PartOfSpeech.NUMERAL: "NUM",
}

#: LexInfo 3.0 named individual per :class:`PartOfSpeech` member (STANDARDS.md § 1b/1c),
#: as local names (LexInfo's ontology individuals, not full URIs; prefix with
#: ``lexinfo:`` to resolve them: `https://lexinfo.net/ontology/3.0/lexinfo#`).
LEXINFO_MAP: dict[PartOfSpeech, str] = {
    PartOfSpeech.NOUN: "lexinfo:noun",
    PartOfSpeech.VERB: "lexinfo:verb",
    PartOfSpeech.ADJECTIVE: "lexinfo:adjective",
    PartOfSpeech.ADVERB: "lexinfo:adverb",
    PartOfSpeech.PRONOUN: "lexinfo:pronoun",
    PartOfSpeech.PREPOSITION: "lexinfo:preposition",
    PartOfSpeech.CONJUNCTION: "lexinfo:conjunction",
    PartOfSpeech.DETERMINER: "lexinfo:determiner",
    PartOfSpeech.INTERJECTION: "lexinfo:interjection",
    PartOfSpeech.NUMERAL: "lexinfo:numeral",
}


def upos_for(entry: Lexeme, pos: PartOfSpeech) -> str:
    """Return the UPOS tag for one of ``entry``'s part-of-speech entries.

    Universal Dependencies tags every proper noun ``PROPN`` regardless of its syntactic
    role (STANDARDS.md § 1c). We do not store that as a POS value — a proper noun's kind
    and :class:`ProperNounInfo` already carry the distinction — so this is a rule applied
    at export time, not a stored value: a noun-POS entry on a :attr:`LexemeKind.PROPER_NOUN`
    lexeme exports as ``PROPN``; everything else exports its plain :attr:`PartOfSpeech.upos`.

    Args:
        entry: The lexeme the POS entry belongs to.
        pos: The part of speech to tag.

    Returns:
        The UPOS tag string.
    """
    if entry.kind is LexemeKind.PROPER_NOUN and pos is PartOfSpeech.NOUN:
        return "PROPN"
    return pos.upos


class ReadingLevel(StrEnum):
    """Target reading level for a rendition."""

    NEUTRAL = "neutral"
    GRADE_1 = "grade_1"
    GRADE_5 = "grade_5"
    GRADE_10 = "grade_10"
    COLLEGE = "college"

    @property
    def crosswalk(self) -> LevelCrosswalk:
        """Return this level's external crosswalk (see :data:`READING_LEVEL_CROSSWALK`)."""
        return READING_LEVEL_CROSSWALK[self]


@dataclass(frozen=True, slots=True)
class LevelCrosswalk:
    """One :class:`ReadingLevel` member's external text-complexity crosswalk.

    Reference only (docs/STANDARDS-PLAN.md § 2, A6): none of these external scales
    substitute for :class:`ReadingLevel` as a *generation-time target*, since they are
    post-hoc measurements of an existing text, not a property a planned gloss can carry
    before it is written (STANDARDS.md § 6d). A dataclass, not a pydantic model, because
    nothing here is ever parsed from or serialised to a stored entry.
    """

    ccss_band: str
    lexile_band: str
    cefr: str
    marc_audience: str
    approx_age: str


#: Reading-level crosswalk to CCSS text-complexity bands, the Lexile Framework, CEFR
#: (L2 proficiency, illustrative only), MARC 008/22 target-audience codes, and
#: approximate reader age, per STANDARDS.md § 6c (CEFR/MARC columns reused verbatim from
#: docs/REGISTERS.md § 7c, as STANDARDS.md itself does). ``grade_1`` has no CCSS/Lexile
#: band of its own — the standards' quantitative scale starts at "2nd-3rd" — so that row
#: is explicitly an extrapolation below the floor, not a sourced figure.
READING_LEVEL_CROSSWALK: dict[ReadingLevel, LevelCrosswalk] = {
    ReadingLevel.NEUTRAL: LevelCrosswalk(
        ccss_band="n/a (register-neutral prose, not grade-banded)",
        lexile_band="n/a",
        cefr="n/a",
        marc_audience="g",
        approx_age="n/a",
    ),
    ReadingLevel.GRADE_1: LevelCrosswalk(
        ccss_band="below 2nd-3rd (no quantitative CCSS band for grade 1; extrapolated)",
        lexile_band="~200L-300L (extrapolated below the 420L floor)",
        cefr="A1",
        marc_audience="a/b",
        approx_age="6-7",
    ),
    ReadingLevel.GRADE_5: LevelCrosswalk(
        ccss_band="4th-5th",
        lexile_band="740L-1010L",
        cefr="A2/B1",
        marc_audience="c",
        approx_age="10-11",
    ),
    ReadingLevel.GRADE_10: LevelCrosswalk(
        ccss_band="9th-10th",
        lexile_band="1050L-1335L",
        cefr="B2",
        marc_audience="d",
        approx_age="15-16",
    ),
    ReadingLevel.COLLEGE: LevelCrosswalk(
        ccss_band="11th-CCR",
        lexile_band="1185L-1385L",
        cefr="C1/C2",
        marc_audience="e/f",
        approx_age="18+",
    ),
}

#: The acceptable Flesch-Kincaid grade band per :class:`ReadingLevel`, the single source
#: of truth for :func:`opengloss_generator.readability.grade_band` (docs/STANDARDS-PLAN.md
#: § 2, A6): the check and this documentation cannot disagree if the check reads its
#: numbers from here. The bands overlap deliberately — adjacent levels should be
#: distinguishable, not disjoint, and a ``grade_10`` rewrite that measures 11.5 is not
#: wrong for being close to college prose. These are project-defined bands, not a direct
#: copy of STANDARDS.md § 6a's CCSS Flesch-Kincaid column: that table has no K-1 row and
#: its bands (e.g. 4.51-7.73 for 4th-5th) are narrower than ours, tuned to *scored,
#: already-written* text rather than a *generation-time* target with a single retry
#: budget. No sourced reason in STANDARDS.md required changing the existing numbers, so
#: they are carried over unchanged from their pre-standards-review values.
FK_BANDS: dict[ReadingLevel, tuple[float, float]] = {
    ReadingLevel.NEUTRAL: (-math.inf, math.inf),
    ReadingLevel.GRADE_1: (-math.inf, 3.0),
    ReadingLevel.GRADE_5: (3.0, 7.0),
    ReadingLevel.GRADE_10: (7.0, 12.0),
    ReadingLevel.COLLEGE: (10.0, math.inf),
}


class Register(StrEnum):
    """Target register for a rendition.

    ``plain``, ``informal``, ``formal``, ``technical``, ``slang`` and ``in_house`` are
    formality/domain registers in the ISO 12620 / TBX sense (DC-423, see
    docs/REGISTERS.md); :data:`TBX_REGISTER_MAP` gives each its DC-423 picklist value
    where one exists. ``marketing`` is the exception: it is kept on this axis, but it is
    a Biber & Conrad *genre* value (a situational/communicative-purpose category, not a
    formality level), retained here rather than split into a third axis so renditions
    stay keyed on two axes. :attr:`is_genre` is ``True`` only for it.

    ``plain`` is documented as TBX ``neutralRegister`` rather than named ``NEUTRAL``
    because :class:`ReadingLevel` already has a ``NEUTRAL`` member, and a
    ``(neutral, neutral)`` key reads badly.

    D-27: the pre-D-27 value ``"professional"`` is still accepted on load (see
    :meth:`_missing_`) and normalises to ``FORMAL``, so old stored data keeps working.
    """

    PLAIN = "plain"
    INFORMAL = "informal"
    FORMAL = "formal"
    TECHNICAL = "technical"
    SLANG = "slang"
    IN_HOUSE = "in_house"
    MARKETING = "marketing"

    @classmethod
    def _missing_(cls, value: object) -> Register | None:
        """Map the retired ``"professional"`` value onto ``FORMAL`` (D-27)."""
        if value == "professional":
            return cls.FORMAL
        return None

    @property
    def is_genre(self) -> bool:
        """Return whether this is a Biber & Conrad situational *genre* value.

        ``True`` only for :attr:`MARKETING` — every other member is a formality or
        domain register, not a communicative-purpose/genre category.
        """
        return self is Register.MARKETING


#: DC-423 (TBX Master Data Category List) picklist value per :class:`Register` member,
#: or ``None`` where no DC-423 value applies (``FORMAL`` is a dictionary-practice label
#: with no DC-423 analogue; ``MARKETING`` is a genre value, not a register). See
#: docs/REGISTERS.md § 1.
TBX_REGISTER_MAP: dict[Register, str | None] = {
    Register.PLAIN: "neutralRegister",
    Register.INFORMAL: "colloquialRegister",
    Register.FORMAL: None,
    Register.TECHNICAL: "technicalRegister",
    Register.SLANG: "slangRegister",
    Register.IN_HOUSE: "in-houseRegister",
    Register.MARKETING: None,
}

#: The formality scale, low to high. ``technical``, ``in_house`` and ``marketing`` are
#: deliberately excluded: technical register is orthogonal to formality (a technical
#: register can be read as formal or casual shoptalk), ``in_house`` is jargon rather
#: than a formality level, and ``marketing`` is a genre value (see
#: :attr:`Register.is_genre`), not a point on this scale.
FORMALITY_ORDER: tuple[Register, ...] = (
    Register.SLANG,
    Register.INFORMAL,
    Register.PLAIN,
    Register.FORMAL,
)


class RelationType(StrEnum):
    """Semantic relation types a sense may assert about another term.

    New relation types are enum values, not schema changes: the wire shape of
    :class:`Relation` is the same for all of them.
    """

    SYNONYM = "synonym"
    ANTONYM = "antonym"
    HYPERNYM = "hypernym"
    HYPONYM = "hyponym"
    MERONYM = "meronym"
    HOLONYM = "holonym"
    DERIVATION = "derivation"
    COLLOCATION = "collocation"
    CONFUSABLE_WITH = "confusable_with"
    SEE_ALSO = "see_also"
    CAUSES = "causes"
    ENTAILS = "entails"
    USED_WITH = "used_with"
    INSTANCE_OF = "instance_of"

    @property
    def namespace(self) -> str:
        """Return which interop vocabulary this relation type belongs to.

        ``"wn"`` for a type the Global WordNet Association / WN-LMF inventory already
        covers (see :data:`WN_RELATION_MAP`), ``"skos"`` for one whose closest interop
        home is a SKOS property instead (see :data:`SKOS_RELATION_MAP`), or ``"og"`` for
        one with no standards home at all — genuinely ours (STANDARDS.md § 2,
        docs/STANDARDS-PLAN.md § 8's B1 reconciliation: no enum rename, export mapping
        only).
        """
        if self in _OG_RELATION_TYPES:
            return "og"
        return "wn"


#: Relation types with no Global WordNet Association or SKOS analogue at all — genuinely
#: lexicographic information a wordnet-style synset/sense-relation model has no slot for
#: (STANDARDS.md § 2c). :attr:`RelationType.namespace` returns ``"og"`` for these.
_OG_RELATION_TYPES: frozenset[RelationType] = frozenset(
    {RelationType.CONFUSABLE_WITH, RelationType.USED_WITH, RelationType.COLLOCATION}
)

#: WN-LMF ``relType`` string per :class:`RelationType` member whose namespace is
#: ``"wn"`` (STANDARDS.md § 2a/2c). ``synonym`` has no bare WN-LMF value — within-synset
#: terms are synonymous by construction — so it maps to ``eq_synonym``, the cross-resource
#: alignment relation closest in spirit. ``meronym``/``holonym`` map to WN-LMF's own
#: unspecified-subtype catch-all values, not the finer ``mero_part``/``holo_member``/etc.
#: split: that split is deferred to a future retrofit pass with its own cost case, per the
#: § 8 reconciliation. ``instance_of`` keeps its name here but is named ``instance_hypernym``
#: in WN-LMF — same concept, different label.
WN_RELATION_MAP: dict[RelationType, str] = {
    RelationType.SYNONYM: "eq_synonym",
    RelationType.ANTONYM: "antonym",
    RelationType.HYPERNYM: "hypernym",
    RelationType.HYPONYM: "hyponym",
    RelationType.MERONYM: "meronym",
    RelationType.HOLONYM: "holonym",
    RelationType.DERIVATION: "derivation",
    RelationType.SEE_ALSO: "also",
    RelationType.CAUSES: "causes",
    RelationType.ENTAILS: "entails",
    RelationType.INSTANCE_OF: "instance_hypernym",
}

#: SKOS property offering a *looser* alternative interop reading for a handful of
#: ``"wn"``-namespaced relation types (STANDARDS.md § 2c) — supplementary to
#: :data:`WN_RELATION_MAP`, not a replacement for it: none of our relation types are
#: exclusively SKOS-shaped, so this map is deliberately partial.
SKOS_RELATION_MAP: dict[RelationType, str] = {
    RelationType.HYPERNYM: "skos:broader",
    RelationType.HYPONYM: "skos:narrower",
    RelationType.SYNONYM: "skos:closeMatch",
    RelationType.SEE_ALSO: "skos:related",
}


class LexemeKind(StrEnum):
    """The top-level node type of a lexeme.

    Sampling, QA, prompt selection and multi-word structure all branch on this, so it is
    a discriminator rather than a tag.
    """

    SIMPLEX = "simplex"
    COMPOUND = "compound"
    PHRASAL_VERB = "phrasal_verb"
    IDIOM = "idiom"
    PROPER_NOUN = "proper_noun"
    ABBREVIATION = "abbreviation"
    AFFIX = "affix"
    FUNCTION_WORD = "function_word"


class EntityType(StrEnum):
    """What kind of thing a proper noun names."""

    PERSON = "person"
    PLACE = "place"
    ORGANIZATION = "organization"
    WORK = "work"
    EVENT = "event"
    PRODUCT = "product"
    SPECIES = "species"
    OTHER = "other"


#: OntoNotes 5 named-entity type per :class:`EntityType` member (STANDARDS.md § 4a/4c),
#: or ``None`` where OntoNotes has no analogue. ``PLACE`` is **lossy**: OntoNotes splits
#: political/administrative places (``GPE``) from physical geography (``LOC``); we map to
#: the common case, ``GPE``, since nothing in the generation pipeline currently
#: disambiguates the two (STANDARDS.md § 4e). ``SPECIES`` has no OntoNotes type at all —
#: none of its 18 types cover taxonomic names — so it stays an explicit, documented gap
#: rather than being force-fit into one; see :data:`SCHEMA_ORG_MAP` for its Schema.org
#: side, which *does* have a dedicated type. ``OTHER`` is a residual catch-all with no
#: standard analogue.
ONTONOTES_MAP: dict[EntityType, str | None] = {
    EntityType.PERSON: "PERSON",
    EntityType.PLACE: "GPE",
    EntityType.ORGANIZATION: "ORG",
    EntityType.WORK: "WORK_OF_ART",
    EntityType.EVENT: "EVENT",
    EntityType.PRODUCT: "PRODUCT",
    EntityType.SPECIES: None,
    EntityType.OTHER: None,
}

#: Schema.org type per :class:`EntityType` member, for export (STANDARDS.md § 4b/4c).
#: ``SPECIES`` maps to Schema.org's dedicated ``Taxon`` type even though it has no
#: OntoNotes analogue (see :data:`ONTONOTES_MAP`); ``OTHER`` falls back to the bare
#: ``Thing`` root, since it is a residual by design with no more specific type to offer.
SCHEMA_ORG_MAP: dict[EntityType, str] = {
    EntityType.PERSON: "Person",
    EntityType.PLACE: "Place",
    EntityType.ORGANIZATION: "Organization",
    EntityType.WORK: "CreativeWork",
    EntityType.EVENT: "Event",
    EntityType.PRODUCT: "Product",
    EntityType.SPECIES: "Taxon",
    EntityType.OTHER: "Thing",
}


class StageName(StrEnum):
    """Generation stages, used for provenance, model routing and cost accounting."""

    OVERVIEW = "overview"
    SENSES = "senses"
    RENDITIONS = "renditions"
    # One call per entry writing N fresh example sentences for every live sense at once
    # (D-53). Distinct from RENDITIONS, which rewrites one existing example per target:
    # this stage writes new sentences, is priced and reserved for a much larger answer,
    # and is the only stage whose output is thrown away wholesale when it fails a check.
    EXAMPLES = "examples"
    SENSE_CHECK = "sense_check"
    ETYMOLOGY = "etymology"
    ENCYCLOPEDIA = "encyclopedia"
    LEXICAL_EXPLANATION = "lexical_explanation"
    CLASSIFY_KIND = "classify_kind"
    HYGIENE = "hygiene"
    TAG_DOMAIN = "tag_domain"
    RESOLVE = "resolve"
    SPANS = "spans"
    FRONTIER = "frontier"
    QA = "qa"


class EntryStatus(StrEnum):
    """Completeness of a stored entry."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    RETIRED = "retired"


class QAFlag(StrEnum):
    """Closed QA flag vocabulary for :attr:`Assessment.qa_flags`.

    Grounded in the MQM Core error typology (STANDARDS.md § 9c), reinterpreted for a
    dictionary-generation pipeline rather than translation QA (§ 9b): each MQM-derived
    member's docstring-adjacent comment below cites its MQM Core parent. The four
    ``og.``-prefixed members are project-specific: three (``HALLUCINATION``, ``OFF_TOPIC``,
    plus the catch-all ``OTHER``) come from STANDARDS.md § 9c's own recommended list, and
    six more (below) are this project's own consistency-check/generation flags with no
    MQM Core analogue at all (docs/STANDARDS-PLAN.md § 3, B3; D-45 adds the fifth, D-51
    the sixth).
    A closed enum means an unrecognised string is rejected by validation rather than
    silently accepted as free text — use :meth:`Assessment.flag` to add one.
    """

    FACTUAL_ERROR = "factual_error"  # MQM Accuracy > Mistranslation
    SCOPE_MISMATCH = "scope_mismatch"  # MQM Accuracy > Overtranslation/Undertranslation
    UNSUPPORTED_ADDITION = "unsupported_addition"  # MQM Accuracy > Addition
    MISSING_CONTENT = "missing_content"  # MQM Accuracy > Omission
    TERMINOLOGY_ERROR = "terminology_error"  # MQM Terminology > Wrong term / Inconsistent use
    GRAMMAR_ERROR = "grammar_error"  # MQM Linguistic conventions > Grammar
    SPELLING_ERROR = "spelling_error"  # MQM Linguistic conventions > Spelling
    PUNCTUATION_ERROR = "punctuation_error"  # MQM Linguistic conventions > Punctuation
    UNINTELLIGIBLE = "unintelligible"  # MQM Linguistic conventions > Unintelligible
    REGISTER_MISMATCH = "register_mismatch"  # MQM Style > Language register
    AWKWARD_STYLE = "awkward_style"  # MQM Style > Awkward/Unidiomatic style
    INCONSISTENT_STYLE = "inconsistent_style"  # MQM Style > Inconsistent style
    AUDIENCE_INAPPROPRIATE = "audience_inappropriate"  # MQM Audience appropriateness
    HALLUCINATION = "hallucination"  # og: no MQM Core analogue (§ 9c)
    OFF_TOPIC = "off_topic"  # og: no MQM Core analogue (§ 9c)
    OTHER = "other"  # catch-all, requires a note

    # Project-specific flags with no MQM Core analogue at all (docs/STANDARDS-PLAN.md § 3).
    OG_HEADWORD_INITIAL = "og.headword_initial"
    OG_ARTIFACT_RELATION = "og.artifact_relation"
    OG_READABILITY_MISS = "og.readability_miss"
    OG_DUPLICATE_GLOSS = "og.duplicate_gloss"
    # An example rendition whose text contains no form of its own headword at all — the
    # model wrote around the word instead of using it (D-45).
    OG_HEADWORD_ABSENT = "og.headword_absent"
    # A grade_1 or grade_5 rendition too many of whose words are not on the familiar-word
    # list, whatever its Flesch-Kincaid grade says (D-51).
    OG_HARD_VOCABULARY = "og.hard_vocabulary"
    # A register rendition of a gloss whose content-word set is at least 90% the same as
    # the canonical gloss's (:func:`~opengloss_generator.hygiene.is_near_copy`) -- a
    # register change in name only, not in wording (D-59). The value has no ``og.`` dot
    # prefix, unlike its siblings above: it is shared with another branch adding the same
    # member concurrently and must match byte-for-byte for the merge to be trivial.
    OG_NEAR_COPY = "og_near_copy"


class _Base(BaseModel):
    """Base model: strict about unknown fields, permissive about nothing else."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        use_enum_values=False,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


class Provenance(_Base):
    """How a piece of content came to exist.

    Recorded per stage rather than per entry, because a single entry is assembled from
    several model calls that may use different models, tiers, and prompt versions.
    """

    stage: StageName
    model: str
    prompt_version: str
    service_tier: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 1
    run_id: str | None = None
    # Free-text detail, e.g. the superseded value a rewrite replaced.
    note: str | None = None
    generated_at: dt.datetime = Field(
        default_factory=lambda: dt.datetime.now(tz=dt.UTC),
    )


class Assessment(_Base):
    """Quality signals attached to a rendition, a sense, or a whole entry."""

    readability_grade: float | None = None
    #: The share of this rendition's words that are not on the Dale-Chall familiar-word
    #: list, 0.0-1.0, measured by :func:`~opengloss_generator.vocabulary.hard_word_share`
    #: with the entry's own headword excused (D-51). Written on every measured rendition
    #: at every level, because it is free and it is the only signal the pipeline has for
    #: whether a reader *knows* the words; acted on only at ``grade_1`` and ``grade_5``,
    #: where :data:`QAFlag.OG_HARD_VOCABULARY` says the share is still over its band.
    #: ``None`` on a rendition written before the measurement existed.
    hard_word_share: float | None = None
    qa_score: float | None = None
    qa_flags: list[QAFlag] = Field(default_factory=list)
    judge_model: str | None = None
    judged_at: dt.datetime | None = None
    human_verified: bool = False

    def flag(self, flag: QAFlag) -> None:
        """Append ``flag`` to :attr:`qa_flags`, unless it is already present.

        Args:
            flag: The flag to add.
        """
        if flag not in self.qa_flags:
            self.qa_flags.append(flag)


class ProperNounInfo(_Base):
    """Entity typing for a lexeme whose kind is :attr:`LexemeKind.PROPER_NOUN`."""

    entity_type: EntityType
    wikidata_qid: Annotated[str | None, Field(default=None, pattern=r"^Q[1-9][0-9]*$")] = None


class RelationTarget(_Base):
    """The far end of a relation.

    A target begins as the surface form the model produced. The ``resolve`` stage later
    fills in ``sense_id`` and ``confidence``; nothing about the target's identity as an
    *edge endpoint* changes, so edge ids survive resolution.
    """

    term: Annotated[str, Field(min_length=1)]
    sense_id: str | None = None
    confidence: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)] = None

    @field_validator("term")
    @classmethod
    def _term_must_be_sluggable(cls, value: str) -> str:
        """Strip the term and require that it yields a non-empty slug.

        Edge identifiers are built from ``slugify(term)``, so a term with no
        alphanumeric content would make :meth:`Lexeme.edges` raise instead of
        validating.
        """
        stripped = value.strip()
        slugify(stripped)
        return stripped

    @property
    def lexeme_id(self) -> str:
        """Return the derived lexeme id of the target term. Never stored."""
        return slugify(self.term)

    @property
    def resolved(self) -> bool:
        """Return whether this target has been resolved to a sense."""
        return self.sense_id is not None


class Relation(_Base):
    """One typed assertion a sense makes about another term."""

    type: RelationType
    target: RelationTarget
    note: str | None = None
    provenance_id: str | None = None

    @model_validator(mode="after")
    def _confusable_requires_note(self) -> Self:
        """Require a note on ``confusable_with``: the note *is* the content."""
        if self.type is RelationType.CONFUSABLE_WITH and not (self.note or "").strip():
            raise ValueError(
                f"relation confusable_with -> {self.target.term!r} requires a note "
                "explaining how the two differ"
            )
        return self


class Example(_Base):
    """A usage example, optionally with the character span of the headword form."""

    text: Annotated[str, Field(min_length=1, max_length=2000)]
    span: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _span_within_text(self) -> Self:
        """Require ``0 <= start < end <= len(text)`` when a span is present."""
        if self.span is None:
            return self
        start, end = self.span
        if not (0 <= start < end <= len(self.text)):
            raise ValueError(
                f"example span {self.span} out of bounds for text of length {len(self.text)}"
            )
        return self

    @property
    def matched(self) -> str | None:
        """Return the substring the span selects, or ``None`` if there is no span."""
        if self.span is None:
            return None
        start, end = self.span
        return self.text[start:end]


class Rendition[T](_Base):
    """One rendering of a field at a given reading level and register.

    The register is serialised under the key ``register``. The Python attribute is
    ``style`` only because ``register`` collides with ``ABCMeta.register`` on every
    Pydantic model class: Pydantic picks the bound method up as an implicit default,
    which silently makes the field optional and breaks JSON-schema generation (D-5).
    """

    reading_level: ReadingLevel
    style: Register = Field(alias="register")
    content: T
    provenance_id: str | None = None
    assessment: Assessment | None = None

    @property
    def key(self) -> tuple[ReadingLevel, Register]:
        """Return the ``(reading_level, register)`` key of this rendition."""
        return (self.reading_level, self.style)

    @property
    def is_canonical(self) -> bool:
        """Return whether this is the canonical ``(neutral, plain)`` rendition."""
        return self.key == CANONICAL_KEY


CANONICAL_KEY: tuple[ReadingLevel, Register] = (ReadingLevel.NEUTRAL, Register.PLAIN)


def _uniqueness_key(rendition: Rendition[Any]) -> tuple[Any, ...]:
    """Return the key a rendition must be unique on within its set.

    Glosses and prose sections carry one text per ``(level, register)``. Examples are
    different: a sense may legitimately have several canonical examples, so their key
    also includes the example text.

    Args:
        rendition: The rendition to key.

    Returns:
        The tuple that must not repeat within a :class:`Renditions` set.
    """
    content = rendition.content
    if isinstance(content, Example):
        return (rendition.reading_level, rendition.style, content.text)
    return (rendition.reading_level, rendition.style)


class Renditions[T](RootModel[list[Rendition[T]]]):
    """A uniqueness-checked set of renditions of one field.

    Serialises as a plain JSON list, so the wire shape is a list of rendition objects
    with no wrapper key.
    """

    root: list[Rendition[T]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_are_unique(self) -> Self:
        """Reject two renditions that share a uniqueness key."""
        seen: set[tuple[Any, ...]] = set()
        for rendition in self.root:
            key = _uniqueness_key(rendition)
            if key in seen:
                raise ValueError(f"duplicate rendition {key}")
            seen.add(key)
        return self

    def __iter__(self) -> Iterator[Rendition[T]]:  # ty: ignore[invalid-method-override]
        """Iterate over the renditions in insertion order.

        This deliberately narrows ``BaseModel.__iter__``, which yields field pairs: a
        rendition set *is* its list, and every caller wants the renditions.
        """
        return iter(self.root)

    def __len__(self) -> int:
        """Return the number of renditions held."""
        return len(self.root)

    def __bool__(self) -> bool:
        """Return whether any rendition is held."""
        return bool(self.root)

    def __getitem__(self, index: int) -> Rendition[T]:
        """Return the rendition at ``index`` in insertion order."""
        return self.root[index]

    def canonical(self) -> Rendition[T] | None:
        """Return the first ``(neutral, plain)`` rendition, or ``None`` if absent."""
        return self.get(*CANONICAL_KEY)

    def get(self, reading_level: ReadingLevel, style: Register) -> Rendition[T] | None:
        """Return the first rendition with the given key, or ``None``.

        Args:
            reading_level: The reading level to look for.
            style: The register to look for (wire name ``register``).

        Returns:
            The matching rendition, or ``None``.
        """
        return next((r for r in self.root if r.key == (reading_level, style)), None)

    def has(self, reading_level: ReadingLevel, style: Register) -> bool:
        """Return whether a rendition with the given key is present."""
        return self.get(reading_level, style) is not None

    def missing(
        self, targets: Iterable[tuple[ReadingLevel, Register]]
    ) -> list[tuple[ReadingLevel, Register]]:
        """Return the requested keys that are not yet present, in request order.

        Args:
            targets: The ``(reading_level, register)`` pairs wanted.

        Returns:
            The subset of ``targets`` that is absent, de-duplicated.
        """
        absent: list[tuple[ReadingLevel, Register]] = []
        for target in targets:
            if not self.has(*target) and target not in absent:
                absent.append(target)
        return absent

    def add(self, rendition: Rendition[T]) -> None:
        """Append a rendition.

        Args:
            rendition: The rendition to add.

        Raises:
            ValueError: If a rendition with the same uniqueness key is already held.
        """
        key = _uniqueness_key(rendition)
        if any(_uniqueness_key(existing) == key for existing in self.root):
            raise ValueError(f"duplicate rendition {key}")
        self.root.append(rendition)


def canonical_rendition[T](content: T, *, provenance_id: str | None = None) -> Rendition[T]:
    """Return a canonical ``(neutral, plain)`` rendition wrapping ``content``.

    Args:
        content: The payload — a gloss string, an :class:`Example`, a prose section.
        provenance_id: Key into the owning entry's provenance table, if known.

    Returns:
        A :class:`Rendition` at ``(neutral, plain)``.
    """
    return Rendition[T](
        reading_level=ReadingLevel.NEUTRAL,
        style=Register.PLAIN,
        content=content,
        provenance_id=provenance_id,
    )


#: A :class:`Sense.concept_id` is either a Global WordNet Association Interlingual
#: Index id (``ili:iNNNNNN``, ``N`` never leading with zero) or a project concept with
#: no ILI counterpart (``og:c-`` followed by 16 lowercase hex digits — see
#: :func:`project_concept_id`). A5, `docs/STANDARDS-PLAN.md` § 2.
_ILI_CONCEPT_ID = re.compile(r"^ili:i[1-9][0-9]*$")
_PROJECT_CONCEPT_ID = re.compile(r"^og:c-[0-9a-f]{16}$")


def project_concept_id(member_sense_ids: Iterable[str]) -> str:
    """Return a deterministic project concept id for a set of member senses.

    The id is derived from the member sense ids themselves, never assigned randomly
    (D-1): sorting before joining makes the result independent of the order the members
    are supplied in, so the same synset always hashes to the same id regardless of which
    caller assembled it or in what order.

    Args:
        member_sense_ids: The sense ids belonging to this concept (order-independent).

    Returns:
        ``"og:c-"`` followed by the first 16 hex characters of the blake2b digest of the
        sorted, comma-joined member ids.
    """
    joined = ",".join(sorted(member_sense_ids))
    digest = hashlib.blake2b(joined.encode("utf-8"), digest_size=8).hexdigest()
    return f"og:c-{digest[:16]}"


class Sense(_Base):
    """A single meaning of a lexeme under one part of speech."""

    index: Annotated[int, Field(ge=0)]
    gloss: Renditions[str]
    examples: Renditions[Example] = Field(default_factory=lambda: Renditions[Example](root=[]))
    relations: list[Relation] = Field(default_factory=list)
    domain: DomainTag | None = None
    secondary_domains: list[DomainTag] = Field(default_factory=list)
    domain_hint: str | None = None
    concept_id: str | None = None
    assessment: Assessment | None = None
    retired: bool = False

    @field_validator("concept_id")
    @classmethod
    def _concept_id_format(cls, value: str | None) -> str | None:
        """Require ``concept_id`` to be an ILI id or a project concept id.

        Args:
            value: The raw ``concept_id`` value, or ``None``.

        Returns:
            ``value`` unchanged, when it validates.

        Raises:
            ValueError: If ``value`` matches neither shape.
        """
        if value is None:
            return None
        if _ILI_CONCEPT_ID.match(value) or _PROJECT_CONCEPT_ID.match(value):
            return value
        raise ValueError(
            f"concept_id {value!r} must match 'ili:i<N>' (Global WordNet ILI id) or "
            "'og:c-<16 hex chars>' (project concept, see project_concept_id())"
        )

    @model_validator(mode="after")
    def _canonical_gloss_required(self) -> Self:
        """Require the canonical ``(neutral, plain)`` gloss on every sense.

        Everything downstream — prompts, exports, the resolver's target list — reads the
        canonical gloss, so a sense without one is not a sense.
        """
        canonical = self.gloss.canonical()
        if canonical is None:
            raise ValueError(f"sense {self.index} has no canonical (neutral, plain) gloss")
        if not canonical.content.strip():
            raise ValueError(f"sense {self.index} has an empty canonical gloss")
        return self

    @classmethod
    def of(cls, index: int, gloss: str, **kwargs: Any) -> Sense:  # noqa: ANN401 - field values
        """Build a sense from a plain canonical gloss string.

        Args:
            index: Zero-based position within the part-of-speech entry.
            gloss: The canonical ``(neutral, plain)`` definition text.
            **kwargs: Any other field values.

        Returns:
            A :class:`Sense` whose gloss set holds exactly the canonical rendition.
        """
        return cls(index=index, gloss=Renditions[str](root=[canonical_rendition(gloss)]), **kwargs)

    def canonical_gloss(self) -> str:
        """Return the canonical definition text.

        Returns:
            The ``(neutral, plain)`` gloss, which validation guarantees is present.

        Raises:
            ValueError: If the canonical gloss is missing, which validation prevents.
        """
        canonical = self.gloss.canonical()
        if canonical is None:
            raise ValueError(f"sense {self.index} has no canonical gloss")
        return canonical.content

    def relations_of(self, relation_type: RelationType) -> list[Relation]:
        """Return this sense's relations of one type, in order.

        Args:
            relation_type: The relation type to select.

        Returns:
            The matching relations.
        """
        return [r for r in self.relations if r.type is relation_type]

    def relation_targets(self) -> set[str]:
        """Return the distinct surface forms this sense points at."""
        return {relation.target.term for relation in self.relations}


class Morphology(_Base):
    """Inflectional and derivational forms for a part-of-speech entry."""

    plural: str | None = None
    past_tense: str | None = None
    past_participle: str | None = None
    present_participle: str | None = None
    third_person_singular: str | None = None
    comparative: str | None = None
    superlative: str | None = None
    derivations: list[str] = Field(default_factory=list)

    def inflected_forms(self) -> list[str]:
        """Return the inflected forms present, in declaration order, without duplicates.

        Used to seed span finding: an example rarely contains the bare headword.

        Returns:
            The non-empty inflected forms, de-duplicated, preserving order.
        """
        candidates = [
            self.plural,
            self.past_tense,
            self.past_participle,
            self.present_participle,
            self.third_person_singular,
            self.comparative,
            self.superlative,
        ]
        forms: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in forms:
                forms.append(candidate)
        return forms


class POSEntry(_Base):
    """All senses of a lexeme under one part of speech."""

    pos: PartOfSpeech
    senses: list[Sense] = Field(default_factory=list)
    morphology: Morphology = Field(default_factory=Morphology)
    collocations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _contiguous_sense_indices(self) -> Self:
        """Require sense indices to be contiguous and ordered from zero.

        Sense identifiers are positional, so a gap or reordering would silently
        re-point every downstream edge.
        """
        expected = list(range(len(self.senses)))
        actual = [s.index for s in self.senses]
        if actual != expected:
            raise ValueError(f"sense indices for {self.pos} must be {expected}, got {actual}")
        return self


#: Non-ISO-639-3 codes for the two reconstructed proto-languages that recur in etymology
#: segments and that ISO 639-3 has no code for at all (``gem`` is the *family* code
#: "Germanic languages," not a code for reconstructed Proto-Germanic — never to be used as
#: if it were). These are Wiktionary's etymology-language codes, the de facto standard
#: every etymological resource that hits this same gap uses (STANDARDS.md § 3a/3b).
#: Populated by a future retrofit pass (A2's etymology-code pass — deferred; this schema
#: field is added ahead of that population, docs/STANDARDS-PLAN.md § 8).
RECONSTRUCTED_LANGUAGE_CODES: frozenset[str] = frozenset({"ine-pro", "gem-pro"})

_ISO_639_3_RE = re.compile(r"^[a-z]{3}$")


class EtymologySegment(_Base):
    """One step in a lexeme's historical development."""

    language: str
    language_code: str | None = None
    form: str
    meaning: str | None = None
    era: str | None = None

    @field_validator("language_code")
    @classmethod
    def _language_code_format(cls, value: str | None) -> str | None:
        """Require a lowercase ISO 639-3 code or a reconstructed-language exception.

        Args:
            value: The raw ``language_code`` value, or ``None``.

        Returns:
            ``value`` unchanged, when it validates.

        Raises:
            ValueError: If ``value`` matches neither shape.
        """
        if value is None:
            return None
        if _ISO_639_3_RE.match(value) or value in RECONSTRUCTED_LANGUAGE_CODES:
            return value
        raise ValueError(
            f"language_code {value!r} must be a lowercase 3-letter ISO 639-3 code or one "
            f"of the reconstructed-language exceptions {sorted(RECONSTRUCTED_LANGUAGE_CODES)}"
            " (STANDARDS.md § 3)"
        )


class Etymology(_Base):
    """A lexeme's etymological trail."""

    summary: str
    segments: list[EtymologySegment] = Field(default_factory=list)
    cognates: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class Edge(_Base):
    """A derived semantic edge. Projected from sense relations; never stored.

    ``edge_id`` is built from the *slug of the target term*, never from the resolved
    sense, so resolving a target does not change the edge's identity.
    """

    edge_id: str
    source_lexeme: str
    source_sense: str
    relation: RelationType
    target: str
    target_sense: str | None = None
    confidence: float | None = None
    pos: PartOfSpeech


class Lexeme(_Base):
    """A complete lexicon entry."""

    schema_version: str = SCHEMA_VERSION
    lexeme_id: str
    headword: str
    language: str = "en"
    kind: LexemeKind
    proper_noun: ProperNounInfo | None = None
    status: EntryStatus = EntryStatus.COMPLETE
    pos_entries: list[POSEntry] = Field(default_factory=list)
    etymology: Etymology | None = None
    encyclopedia: Renditions[str] = Field(default_factory=lambda: Renditions[str](root=[]))
    lexical_explanation: Renditions[str] = Field(default_factory=lambda: Renditions[str](root=[]))
    is_stopword: bool = False
    frequency: float | None = None
    zipf: float | None = None
    frequency_corpus: str | None = None
    frequency_corpus_tokens: int | None = None
    discovered_from: str | None = None
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    assessment: Assessment | None = None
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(tz=dt.UTC))
    updated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(tz=dt.UTC))

    @model_validator(mode="after")
    def _id_matches_headword(self) -> Self:
        """Require ``lexeme_id`` to be the slug of ``headword``."""
        expected = slugify(self.headword)
        if self.lexeme_id != expected:
            raise ValueError(f"lexeme_id {self.lexeme_id!r} != slug(headword) {expected!r}")
        return self

    @model_validator(mode="after")
    def _unique_pos(self) -> Self:
        """Reject a repeated part of speech, which would collide on ``pos_entry_id``."""
        tags = [entry.pos for entry in self.pos_entries]
        if len(tags) != len(set(tags)):
            raise ValueError(f"duplicate part-of-speech entries: {tags}")
        return self

    @model_validator(mode="after")
    def _proper_noun_block_matches_kind(self) -> Self:
        """Require the ``proper_noun`` block exactly when ``kind`` is ``proper_noun``."""
        is_proper = self.kind is LexemeKind.PROPER_NOUN
        if is_proper and self.proper_noun is None:
            raise ValueError("kind is proper_noun but proper_noun block is missing")
        if not is_proper and self.proper_noun is not None:
            raise ValueError(f"proper_noun block present but kind is {self.kind.value}")
        return self

    @model_validator(mode="after")
    def _function_words_are_stopwords(self) -> Self:
        """Force ``is_stopword`` for function words; the kind implies it."""
        if self.kind is LexemeKind.FUNCTION_WORD:
            self.is_stopword = True
        return self

    @classmethod
    def empty(
        cls,
        headword: str,
        *,
        kind: LexemeKind = LexemeKind.SIMPLEX,
        **kwargs: Any,  # noqa: ANN401 - field values
    ) -> Lexeme:
        """Return a minimal valid entry for a headword.

        Args:
            headword: The surface form.
            kind: The lexeme kind; defaults to ``simplex``.
            **kwargs: Any additional field values to set.

        Returns:
            A :class:`Lexeme` with a derived id and no senses.
        """
        return cls(lexeme_id=slugify(headword), headword=headword, kind=kind, **kwargs)

    def add_provenance(self, provenance: Provenance) -> str:
        """Add a provenance record to the entry's table and return its id.

        Ids are ``p1``, ``p2``, … assigned in insertion order and never reused within an
        entry, so a rendition's ``provenance_id`` stays valid as the table grows.

        Args:
            provenance: The record to store.

        Returns:
            The id under which it was stored.
        """
        index = len(self.provenance) + 1
        while f"p{index}" in self.provenance:
            index += 1
        key = f"p{index}"
        self.provenance[key] = provenance
        return key

    def pos_ids(self) -> list[str]:
        """Return derived identifiers for every part-of-speech entry."""
        return [pos_entry_id(self.lexeme_id, entry.pos.value) for entry in self.pos_entries]

    def iter_senses(self) -> list[tuple[POSEntry, Sense, str]]:
        """Return ``(pos_entry, sense, sense_id)`` for every sense, retired included."""
        return [
            (entry, sense, sense_id(self.lexeme_id, entry.pos.value, sense.index))
            for entry in self.pos_entries
            for sense in entry.senses
        ]

    def rendition_ids(self) -> list[str]:
        """Return derived identifiers for every keyed rendition on this entry.

        Covers the sense glosses and the two entry-level prose sections. Example
        renditions are deliberately absent: several examples may share one
        ``(level, register)`` key, so they have no unique positional id.

        Returns:
            Rendition identifiers, senses first, in document order.
        """

        def ids_for(owner: str, renditions: Renditions[str]) -> list[str]:
            return [rendition_id(owner, r.reading_level.value, r.style.value) for r in renditions]

        ids = [
            rendition_id(sid, r.reading_level.value, r.style.value)
            for _, sense, sid in self.iter_senses()
            for r in sense.gloss
        ]
        ids += ids_for(encyclopedia_owner_id(self.lexeme_id), self.encyclopedia)
        ids += ids_for(explanation_owner_id(self.lexeme_id), self.lexical_explanation)
        return ids

    def edges(self) -> list[Edge]:
        """Project this entry's sense relations onto a flat edge list.

        Returns:
            One :class:`Edge` per ``(sense, relation)`` pair, excluding retired senses.
        """
        projected: list[Edge] = []
        for entry, sense, sid in self.iter_senses():
            if sense.retired:
                continue
            for relation in sense.relations:
                target = relation.target
                projected.append(
                    Edge(
                        edge_id=edge_id(sid, relation.type.value, target.lexeme_id),
                        source_lexeme=self.lexeme_id,
                        source_sense=sid,
                        relation=relation.type,
                        target=target.term,
                        target_sense=target.sense_id,
                        confidence=target.confidence,
                        pos=entry.pos,
                    )
                )
        return projected

    def relation_targets(self) -> set[str]:
        """Return the distinct surface forms this entry points at."""
        return {edge.target for edge in self.edges()}

    def sense_count(self) -> int:
        """Return the number of non-retired senses."""
        return sum(1 for _, sense, _ in self.iter_senses() if not sense.retired)

    def compute_zipf(self) -> None:
        """Fill ``zipf`` from ``frequency`` and ``frequency_corpus_tokens``, in place.

        Both fields are required to produce a value (see A3, `docs/STANDARDS-PLAN.md`
        § 2): a raw count with no known corpus size cannot be scaled. When either is
        missing, ``zipf`` is left as ``None`` rather than guessed at.
        """
        if self.frequency is None or self.frequency_corpus_tokens is None:
            return
        self.zipf = zipf_scale(round(self.frequency), self.frequency_corpus_tokens)

    def touch(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = dt.datetime.now(tz=dt.UTC)
