"""Model-facing output schemas.

These are deliberately separate from ``schema.py``. The storage schema carries derived
identifiers, provenance, and timestamps that a model must never invent; these contracts
carry only what a model is actually being asked to produce. Keeping them apart is what
lets the storage schema use ``extra="forbid"`` without the model's shape dictating it.

Two v3 rules govern the shapes below (``docs/SCHEMA-V3.md`` § 5):

* **Enums do the constraining.** ``DomainTag``, ``RelationType``, ``LexemeKind`` and
  ``EntityType`` appear as typed fields, so structured output makes an out-of-vocabulary
  answer impossible rather than merely discouraged. That is why the domain taxonomy needs
  no free-text validation pass.
* **The model is never asked for anything derivable.** Character spans, sense ids and
  provenance ids are computed by ``spans.py``, ``identity.py`` and the workflows; the
  contracts here ask only for text and choices. The one exception is
  :class:`DraftSpanBatch`, which exists solely as the fallback for examples the
  deterministic finder could not place.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from opengloss_generator.schema import (
    EntityType,
    LexemeKind,
    PartOfSpeech,
    QAFlag,
    ReadingLevel,
    Register,
    RelationType,
)
from opengloss_generator.taxonomy import DomainTag

__all__ = [
    "DraftConfusable",
    "DraftDomainTags",
    "DraftEncyclopedia",
    "DraftEtymology",
    "DraftExampleBatch",
    "DraftKindBatch",
    "DraftKindVerdict",
    "DraftLexicalExplanation",
    "DraftOverview",
    "DraftPOSPlan",
    "DraftProperNoun",
    "DraftQAVerdict",
    "DraftRelation",
    "DraftRendition",
    "DraftRenditionSet",
    "DraftRenditionVerdict",
    "DraftResolution",
    "DraftSense",
    "DraftSenseDomain",
    "DraftSenseExample",
    "DraftSenseSet",
    "DraftSenseVerdict",
    "DraftSpan",
    "DraftSpanBatch",
    "DraftTargetResolution",
    "FrontierJudgement",
    "FrontierVerdict",
    "RelatedTerms",
]

MAX_RELATIONS_PER_SENSE = 20
MAX_CONFUSABLES_PER_SENSE = 3
MAX_SECONDARY_DOMAINS = 2
KIND_BATCH_SIZE = 50
RESOLVE_BATCH_SIZE = 40
SPAN_BATCH_SIZE = 40
QA_MAX_SENSES = 8
#: The judge's rendition sample is four per sense (three gloss levels/registers and
#: one example) plus the two entry-level encyclopedia openings, so a full eight-sense
#: entry produces exactly this many. Sized to fit that worst case rather than rounded,
#: so no sampled rendition is ever shown without room for a verdict about it.
QA_MAX_RENDITIONS = QA_MAX_SENSES * 4 + 2
QA_MAX_FLAGS = 8
QA_ISSUE_CHARS = 120
QA_NOTES_CHARS = 300


def _truncate_notes(value: object) -> object:
    """Clip an over-long judge note rather than failing the whole verdict."""
    return value[:QA_NOTES_CHARS] if isinstance(value, str) else value


class _Draft(BaseModel):
    """Base for model-facing contracts."""

    model_config = ConfigDict(
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


# --------------------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------------------


class DraftPOSPlan(_Draft):
    """One part of speech the headword supports, and how many senses it needs."""

    pos: PartOfSpeech
    sense_count: Annotated[int, Field(ge=1, le=8)]
    note: str | None = Field(default=None, description="Why this POS applies, one clause.")


class DraftProperNoun(_Draft):
    """Entity typing for a headword the overview stage judged to be a proper noun."""

    entity_type: EntityType
    wikidata_qid: Annotated[
        str | None,
        Field(
            default=None,
            description="Wikidata item id such as Q937, or null if you are not certain.",
        ),
    ] = None


class DraftOverview(_Draft):
    """The overview stage's plan for an entry.

    ``domain`` stays free text here and is *not* the controlled tag: the overview call
    does not carry the taxonomy, and a hint costs a handful of tokens. It is stored as
    ``Sense.domain_hint`` and passed to the senses prompt; the binding tag comes from
    :attr:`DraftSense.domain`, where the enum constrains it.
    """

    headword: str
    kind: LexemeKind = Field(
        description=(
            "Structural kind of the HEADWORD, not its part of speech: simplex (one word), "
            "compound, phrasal_verb, idiom, proper_noun, abbreviation, affix, or function_word."
        )
    )
    proper_noun: DraftProperNoun | None = Field(
        default=None,
        description="Fill this in only when kind is proper_noun; otherwise leave it null.",
    )
    is_stopword: bool = False
    domain: str | None = Field(default=None, description="Primary subject domain, if any.")
    pos_plans: Annotated[list[DraftPOSPlan], Field(min_length=1, max_length=6)]


# --------------------------------------------------------------------------------------
# Senses
# --------------------------------------------------------------------------------------


class DraftRelation(_Draft):
    """One typed relation from a sense to another term.

    Replaces the six parallel lists of v2. A new relation type is now an enum value, not
    another list on this contract — and one list costs fewer output tokens than six keys
    of which four are usually empty.
    """

    type: RelationType
    term: Annotated[str, Field(min_length=1, max_length=60)]


class DraftConfusable(_Draft):
    """A term a learner is likely to confuse with this sense, and the distinction."""

    term: Annotated[str, Field(min_length=1, max_length=60)]
    how_they_differ: Annotated[str, Field(min_length=10, max_length=240)]


class DraftSense(_Draft):
    """One generated sense.

    Examples are plain sentences. The headword's character span inside each one is found
    afterwards by :func:`opengloss_generator.spans.find_span` using the entry's
    morphology, so the model is never asked for an offset it would have to count out.
    """

    gloss: Annotated[str, Field(min_length=10, max_length=400)]
    examples: Annotated[list[str], Field(min_length=1, max_length=3)]
    domain: DomainTag
    secondary_domains: Annotated[list[DomainTag], Field(max_length=MAX_SECONDARY_DOMAINS)] = []
    relations: Annotated[list[DraftRelation], Field(max_length=MAX_RELATIONS_PER_SENSE)] = []
    confusables: Annotated[list[DraftConfusable], Field(max_length=MAX_CONFUSABLES_PER_SENSE)] = []


class DraftSenseSet(_Draft):
    """All senses generated for one part of speech, plus its shared morphology."""

    pos: PartOfSpeech
    senses: Annotated[list[DraftSense], Field(min_length=1, max_length=8)]
    collocations: Annotated[list[str], Field(max_length=6)] = []
    plural: str | None = None
    past_tense: str | None = None
    past_participle: str | None = None
    present_participle: str | None = None
    third_person_singular: str | None = None
    comparative: str | None = None
    superlative: str | None = None
    derivations: Annotated[list[str], Field(max_length=8)] = []


# --------------------------------------------------------------------------------------
# Renditions
# --------------------------------------------------------------------------------------


class DraftRendition(_Draft):
    """One rendering of a text field at a target reading level and register."""

    reading_level: ReadingLevel
    # Wire name ``register``; the attribute is ``style`` for the reason given in D-5.
    style: Register = Field(alias="register")
    content: Annotated[str, Field(min_length=3, max_length=6000)]


class DraftRenditionSet(_Draft):
    """Every requested rendition of one field of one owner, generated together.

    Generic over the target field rather than one contract per field: the payload is the
    same shape for a gloss, an example sentence, the encyclopedia section and the usage
    note, so ``enrich`` is one uniform operation and the four fields share one prompt
    cache prefix. ``content`` is always a string — for the ``examples`` field it is the
    rewritten sentence, whose span is found post-hoc.

    Generated as a set rather than one call per target: the model can see its own sibling
    outputs, so the renditions differentiate instead of converging on one middle
    register, and it costs a single prompt instead of N.
    """

    renditions: Annotated[list[DraftRendition], Field(min_length=1, max_length=25)]


# --------------------------------------------------------------------------------------
# Retrofit and resolution passes
# --------------------------------------------------------------------------------------


#: Ceiling on how many sentences one ``examples`` call may return (D-53). An entry with
#: eight senses asking for eight sentences each needs 64; the cap was originally set
#: well above that (200) so a many-sense entry is never silently truncated by the
#: contract, and the real limit on a call's size is the stage's ``max_tokens``.
#:
#: **D-64 found this ceiling is what makes ``DraftExampleBatch`` unusable on Gemini.**
#: A live bisection against ``gemini-3.8-flash`` (reproduced identically against
#: ``gemini-3.7-flash``, the original writer-diversity pilot's task-(b) failure) found
#: Gemini's structured-output translation returns ``400 INVALID_ARGUMENT`` once
#: ``list[DraftSenseExample]``'s declared ``maxItems`` reaches a threshold that depends
#: on the *encoded size* of the item schema, not on any single field or nesting depth,
#: and — importantly — on which output mode carries the schema:
#:
#: * With a bare ``output_type=`` (pydantic-ai's tool-call path, the OpenAPI-subset
#:   transformer), the real ``DraftSenseExample`` schema's cutoff is **54 succeeds, 55
#:   fails**; a hand-built variant with the same shape but plain ``str`` fields instead
#:   of the ``ReadingLevel``/``Register`` enums (far shorter schema text, since the
#:   enums' long docstrings render as JSON-Schema ``description`` text) has a much
#:   higher cutoff, **97 succeeds, 98 fails** — proving ``maxItems`` itself isn't the
#:   special number, total schema weight is.
#: * ``stages.py`` actually calls every stage with ``NativeOutput(output_type,
#:   strict=True)`` (the full-JSON-Schema path, not the tool-call path above), and that
#:   path's real threshold is **lower**: reproduced on the real ``DraftExampleBatch``
#:   contract with the real D-53 examples prompt and instructions, **32 succeeds**, 40
#:   fails validation (``UnexpectedModelBehavior: Exceeded maximum output retries``,
#:   a different failure mode — the model's answer itself, not the schema, was
#:   rejected), and 48 reproduces the hard ``400 INVALID_ARGUMENT`` schema rejection.
#:   This is the mode that matters, since it is what production actually sends.
#:
#: Every other feature tried (the ``sense_ref`` integer field, its ``ge=1`` bound, the
#: aliased ``register`` field) made no difference in isolation. Bisection detail in
#: `docs/WRITER-DIVERSITY.md` Round 2.
#:
#: 32 is the largest value confirmed to work end-to-end against the real contract, the
#: real prompt, and the real ``NativeOutput(strict=True)`` call shape. **Known, accepted
#: regression, larger than it first looks**: an entry needs at most four full
#: eight-sentence senses (``ExamplesConfig.per_sense=8``) to fit; more than four *live*
#: senses summed across every one of its part-of-speech entries — 22 of the 300
#: writer-diversity-pilot sample entries (7.3%), and plausibly more common in
#: `data/core-store`, whose entries are not capped at 8 senses per part of speech the
#: way this sample happens to be — now fails ``DraftExampleBatch`` validation for
#: *every* writer, not only Gemini, because this ceiling is shared code, not
#: provider-specific. **This is a pilot-scoped compromise, not a value to carry into
#: production.** The correct fix is provider-aware: either shape the schema Gemini
#: receives differently from what other providers receive (analogous to how
#: `router.py` already keeps flex-tier and prompt-cache settings OpenAI-only), or have
#: `workflows/examples.py` split a many-sense entry's live senses across more than one
#: call when the active writer is a Google model. Neither was built here — it is a
#: workflow-shape change, not a contract-constant change, and does not fit this
#: pilot's "minimal, additive" bar.
MAX_EXAMPLE_SENTENCES = 32


class DraftSenseExample(_Draft):
    """One fresh example sentence written for one listed sense at one target (D-53).

    ``sense_ref`` is the number the sense was listed under in the prompt, the convention
    :class:`DraftSenseDomain` and :class:`DraftSenseVerdict` already use and for the same
    reason: one small integer is cheaper than a part of speech plus an index, and it
    cannot disagree with the list the model was shown.

    No span field, for the reason the module docstring gives: the headword's offsets
    inside the sentence are found afterwards by
    :func:`opengloss_generator.spans.find_span`, and the workflow rejects the sentence
    outright when they cannot be.
    """

    sense_ref: Annotated[int, Field(ge=1)]
    reading_level: ReadingLevel
    # Wire name ``register``; the attribute is ``style`` for the reason given in D-5.
    style: Register = Field(alias="register")
    text: Annotated[str, Field(min_length=10, max_length=400)]


class DraftExampleBatch(_Draft):
    """Every fresh example sentence for one entry, produced in a single call (D-53).

    One call per *entry*, not per sense: the model is shown every live sense at once, so
    a sentence it writes for sense 2 is written knowing what sense 1 and sense 3 mean,
    which is the only way "this sentence must fit ONLY the sense it is filed under" can be
    asked for at generation time. It is also what keeps the input:output ratio low — one
    ~700-token prompt buys forty verified sentences rather than four.
    """

    examples: Annotated[
        list[DraftSenseExample], Field(min_length=1, max_length=MAX_EXAMPLE_SENTENCES)
    ]


class DraftKindVerdict(_Draft):
    """The lexeme kind of one headword the deterministic rules could not decide.

    Deliberately carries no entity type: the deterministic classifier already catches
    capitalised proper nouns, so the residue reaching this stage is almost entirely
    multi-word forms. Asking every verdict for an entity type would spend output tokens
    on a field that is null nearly every time; ``retrofit`` fills
    ``ProperNounInfo(entity_type=other)`` for the rare proper noun that lands here.
    """

    term: Annotated[str, Field(min_length=1)]
    kind: LexemeKind


class DraftKindBatch(_Draft):
    """Verdicts for a batch of headwords, in the order given."""

    verdicts: Annotated[list[DraftKindVerdict], Field(min_length=1, max_length=KIND_BATCH_SIZE)]


class DraftSenseDomain(_Draft):
    """The controlled domain tags for one sense.

    ``sense_ref`` is the number the sense was listed under in the prompt, not a sense
    index or id: one small integer is cheaper than repeating a part of speech and an
    index per sense, and it cannot disagree with the list the model was shown.
    """

    sense_ref: Annotated[int, Field(ge=1)]
    domain: DomainTag
    secondary_domains: Annotated[list[DomainTag], Field(max_length=MAX_SECONDARY_DOMAINS)] = []


class DraftDomainTags(_Draft):
    """Domain tags for every sense of one entry, produced in a single call."""

    tags: Annotated[list[DraftSenseDomain], Field(min_length=1)]


class DraftTargetResolution(_Draft):
    """Which sense of a target term one relation actually points at.

    ``target_ref`` and ``sense_choice`` are both positions in the lists the prompt
    showed: the target's number, and the number of the candidate sense under it.
    ``sense_choice`` is null when none of the candidates is the right meaning.

    Deliberately no free-text field (no ``reason``/``explanation``): every prose field on
    a structured-output contract is an invitation the model accepts, and RESOLVE_INSTRUCTIONS
    already tells it the answer is the choice and the confidence, nothing else
    (docs/COST-MODEL.md, resolve row — a live run at the old three-sentence instructions
    measured ~660 output tokens/call against a ~48-150 token contract this shape needs).
    """

    target_ref: Annotated[int, Field(ge=1)]
    sense_choice: Annotated[
        int | None, Field(default=None, ge=0, description="Null when no candidate fits.")
    ] = None
    confidence: Annotated[
        float, Field(ge=0.0, le=1.0, description="0-1, per the instructions' three bands.")
    ] = 0.0


class DraftResolution(_Draft):
    """Resolutions for a batch of relation targets belonging to one source entry."""

    resolutions: Annotated[
        list[DraftTargetResolution], Field(min_length=1, max_length=RESOLVE_BATCH_SIZE)
    ]


class DraftSpan(_Draft):
    """Character offsets of the headword form inside one example sentence."""

    example_ref: Annotated[int, Field(ge=1)]
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=1)]


class DraftSpanBatch(_Draft):
    """Spans for the examples the deterministic finder could not place."""

    spans: Annotated[list[DraftSpan], Field(min_length=1, max_length=SPAN_BATCH_SIZE)]


# --------------------------------------------------------------------------------------
# Long-form sections and the walk
# --------------------------------------------------------------------------------------


class DraftEtymologyStep(_Draft):
    """One step in an etymological trail."""

    language: str
    form: str
    meaning: str | None = None
    era: str | None = None


class DraftEtymology(_Draft):
    """The etymology stage's output."""

    summary: Annotated[str, Field(min_length=20, max_length=1200)]
    segments: Annotated[list[DraftEtymologyStep], Field(max_length=8)] = []
    cognates: Annotated[list[str], Field(max_length=8)] = []


class DraftEncyclopedia(_Draft):
    """The encyclopedia stage's output."""

    text: Annotated[str, Field(min_length=100, max_length=6000)]


class DraftLexicalExplanation(_Draft):
    """A short plain-language usage note."""

    text: Annotated[str, Field(min_length=20, max_length=800)]


class FrontierVerdict(_Draft):
    """Whether one candidate string is a real headword worth generating."""

    term: str
    is_headword: bool
    reason: Annotated[str, Field(max_length=200)]


class FrontierJudgement(_Draft):
    """Verdicts for a batch of frontier candidates."""

    verdicts: Annotated[list[FrontierVerdict], Field(min_length=1)]


class RelatedTerms(_Draft):
    """Additional related terms proposed during a graph walk."""

    terms: Annotated[list[str], Field(max_length=25)]


# --------------------------------------------------------------------------------------
# The QA judge
# --------------------------------------------------------------------------------------


class DraftSenseVerdict(_Draft):
    """One judged sense: six independent booleans, and only the prose a failure needs.

    ``sense_ref`` is the number the sense was listed under in the prompt, the same
    convention :class:`DraftSenseDomain` uses and for the same reason: one small integer
    is cheaper than a part of speech plus an index, and it cannot disagree with the list
    the model was shown.

    The booleans are what the metrics aggregate — a defect rate per dimension is only
    meaningful if every judged sense answers every dimension — so none of them is
    optional. The two free-text fields are, and are capped hard: a judge given room to
    narrate spends its output budget narrating (``RESOLVE_INSTRUCTIONS``' own finding,
    D-38), and everything the pipeline can act on is already in the booleans.
    """

    sense_ref: Annotated[int, Field(ge=1)]
    gloss_accurate: bool
    gloss_issue: Annotated[
        str | None,
        Field(default=None, max_length=QA_ISSUE_CHARS, description="Null when accurate."),
    ] = None
    distinct_from_other_senses: bool
    examples_natural: bool
    examples_fit_sense: bool
    relations_valid: bool
    invalid_relations: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=60)]],
        Field(max_length=MAX_RELATIONS_PER_SENSE, description="Target terms only, no types."),
    ] = []
    domain_fits: bool
    suggested_domain: Annotated[
        str | None,
        Field(default=None, max_length=60, description="Null unless domain_fits is false."),
    ] = None


class DraftRenditionVerdict(_Draft):
    """One judged rendition from the sample the prompt showed.

    ``faithful`` is about meaning (does this say what the canonical text says?),
    ``level_appropriate`` about the reading level it is labelled with, and
    ``register_appropriate`` about the register. They are separate because the pipeline's
    remedies are separate: an unfaithful rendition has to be regenerated, an
    out-of-level one only simplified.
    """

    rendition_ref: Annotated[int, Field(ge=1)]
    faithful: bool
    level_appropriate: bool
    register_appropriate: bool
    issue: Annotated[
        str | None,
        Field(default=None, max_length=QA_ISSUE_CHARS, description="Null when all three hold."),
    ] = None


class DraftQAVerdict(_Draft):
    """The judge's whole verdict on one entry.

    ``flags`` is typed as the :class:`~opengloss_generator.schema.QAFlag` enum rather
    than free text, so structured output makes an out-of-vocabulary flag impossible —
    the same rule the domain taxonomy relies on, and the point of adopting a closed
    MQM-grounded list at all (``docs/STANDARDS.md`` § 9d): free-text flags cannot be
    aggregated or used to drive targeted regeneration.
    """

    entry_score: Annotated[int, Field(ge=0, le=100)]
    sense_verdicts: Annotated[list[DraftSenseVerdict], Field(max_length=QA_MAX_SENSES)] = []
    rendition_verdicts: Annotated[
        list[DraftRenditionVerdict], Field(max_length=QA_MAX_RENDITIONS)
    ] = []
    encyclopedia_accurate: bool
    encyclopedia_issue: Annotated[
        str | None,
        Field(default=None, max_length=QA_ISSUE_CHARS, description="Null when accurate."),
    ] = None
    flags: Annotated[list[QAFlag], Field(max_length=QA_MAX_FLAGS)] = []
    # A verdict is not worth a retry over an overlong note: 4 of 60 verdicts retried on
    # this in QA-DIARY iteration 4. Anything past the cap is truncated on the way in.
    notes: Annotated[str, BeforeValidator(_truncate_notes), Field(max_length=QA_NOTES_CHARS)] = ""
