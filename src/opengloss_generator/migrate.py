"""Upgrade older OpenGloss payloads to the v3 schema.

Two source shapes matter:

* **v2.0** — this project's previous schema: one canonical ``gloss`` string per sense,
  six parallel relation lists, ``variants`` keyed by ``(reading_level, register)``.
* **v1.3** — the working store at ``/nas4/data/workspace/curriculum/data/lexicon``:
  ``word`` + ``entries[].senses[].definition``, a random ``uuid4`` on every node, and a
  materialised ``edges`` list.

Both directions share three rules:

1. **Senses are never renumbered.** Sense ids are positional (D-1), so migration
   preserves source order and assigns ``0..n-1`` in that order.
2. **Random identifiers are dropped**, not carried. v3 ids are derived.
3. **Nothing is invented.** A legacy free-text domain that is not in the taxonomy's
   legacy map lands in ``domain_hint`` for the ``tag_domain`` stage to resolve, rather
   than being guessed at.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from opengloss_generator.identity import slugify
from opengloss_generator.schema import (
    Assessment,
    EntityType,
    EntryStatus,
    Etymology,
    EtymologySegment,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Provenance,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.spans import find_span
from opengloss_generator.taxonomy import ROOTS, DomainTag, legacy_domain

__all__ = [
    "FUNCTION_WORDS",
    "SchemaVersion",
    "classify_kind_deterministic",
    "detect_version",
    "entry_evidence",
    "from_v2",
    "from_v13",
    "migrate",
]

SchemaVersion = Literal["1.3", "2.0", "3.0"]

#: Closed-class English words. A headword on this list is a function word regardless of
#: what else it might also be; the list is deliberately small and conservative, because
#: anything ambiguous is cheaper to send to the batched classifier than to get wrong.
FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "and", "but", "or", "nor", "so", "yet", "for",
        "if", "because", "although", "though", "unless", "until", "while", "whereas",
        "that", "than", "as", "whether",
        "i", "me", "my", "mine", "myself",
        "you", "your", "yours", "yourself", "yourselves",
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "we", "us", "our", "ours", "ourselves",
        "they", "them", "their", "theirs", "themselves",
        "who", "whom", "whose", "which", "what",
        "this", "these", "those",
        "am", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "done",
        "have", "has", "had", "having",
        "can", "could", "shall", "should", "will", "would", "may", "might", "must",
        "at", "by", "from", "in", "into", "of", "off", "on", "onto", "out", "over",
        "to", "under", "up", "with", "without", "within", "about", "above", "after",
        "against", "along", "among", "around", "before", "behind", "below", "beneath",
        "beside", "between", "beyond", "during", "except", "near", "since", "through",
        "throughout", "toward", "towards", "upon", "via",
        "not", "no", "none", "both", "each", "either", "neither", "every",
        "all", "any", "some", "such", "there", "here",
    }
)  # fmt: skip

_ABBREVIATION_MAX_LETTERS = 3

#: Characters that may sit between a sentence break and a mention of the headword
#: without carrying any capitalisation signal of their own: markdown emphasis, heading,
#: bullet and quote markers, and opening quotes or brackets. A newline is deliberately
#: absent — it *is* a sentence break here, because legacy prose is markdown and a line
#: or list-item start capitalises exactly as a sentence start does. Scanning past a
#: bullet is safe even mid-word ("co-London"), because the scan then reaches the letter
#: before the hyphen and stops there.
_MARKUP_PREFIX: frozenset[str] = frozenset(
    " \t\r*_#>~-+\u2022\u00b7\u2014\u2013\"'\u201c\u201d\u2018\u2019([{"
)

#: Characters after which a capital is forced by grammar rather than chosen.
_SENTENCE_BREAK: frozenset[str] = frozenset(".!?\n")

#: How many non-sentence-initial capitalised mentions the evidence must carry before a
#: lowercase headword is promoted to a proper noun. Two, not one, because a single
#: mention is as likely to be an eponym ("Named after Albert Einstein") as a name.
_EVIDENCE_MIN_CAPITALS = 2

_V13_SKIP_POS: frozenset[str] = frozenset({"", "unknown", "other"})

#: The Hugging Face dataset + split ``wiki_frequency`` counts were drawn from, per
#: `/nas4/data/workspace/curriculum/src/curriculum/pipelines/wiki_frequency.py`
#: (``load_dataset("wikimedia/wikipedia", "20231101.en")``). A3, `docs/STANDARDS-PLAN.md`
#: § 2: the v1.3 pipeline never recorded the corpus's total token count, so
#: ``frequency_corpus_tokens`` — and therefore ``zipf`` — stays ``None`` until that is
#: recovered or recomputed separately.
_V13_FREQUENCY_CORPUS = "wikimedia/wikipedia:20231101.en"

#: v2 stage names that v3 renamed. Provenance is history, so it must keep validating.
_LEGACY_STAGES: dict[str, str] = {"variants": StageName.RENDITIONS.value}


def detect_version(payload: dict[str, Any]) -> SchemaVersion:
    """Return the schema version of a raw entry payload.

    Args:
        payload: A decoded JSON object.

    Returns:
        ``"1.3"``, ``"2.0"`` or ``"3.0"``.

    Raises:
        ValueError: If the payload matches none of the known shapes.
    """
    declared = payload.get("schema_version")
    if isinstance(declared, str):
        major = declared.split(".", 1)[0]
        if major == "3":
            return "3.0"
        if major == "2":
            return "2.0"
    if "word" in payload and "entries" in payload:
        return "1.3"
    if "headword" in payload and "pos_entries" in payload:
        return "3.0" if "kind" in payload else "2.0"
    raise ValueError("payload matches no known OpenGloss schema version")


def migrate(payload: dict[str, Any]) -> Lexeme:
    """Upgrade any recognised payload to a v3 :class:`Lexeme`.

    Args:
        payload: A decoded JSON object in v1.3, v2.0 or v3.0 shape.

    Returns:
        The validated v3 entry. A v3 payload is simply validated.
    """
    version = detect_version(payload)
    if version == "1.3":
        return from_v13(payload)
    if version == "2.0":
        return from_v2(payload)
    return Lexeme.model_validate(payload)


# --------------------------------------------------------------------------------------
# Kind classification
# --------------------------------------------------------------------------------------


def _headword_pattern(headword: str) -> re.Pattern[str]:
    """Return a case-insensitive whole-word pattern for a possibly multi-word headword.

    Args:
        headword: The surface form; internal whitespace matches any run of whitespace,
            so a phrase still matches across a line wrap.

    Returns:
        The compiled pattern.
    """
    tokens = [re.escape(token) for token in headword.split()]
    return re.compile(r"\b" + r"\s+".join(tokens) + r"\b", re.IGNORECASE)


def _capitalisation_counts(headword: str, evidence: str) -> tuple[int, int]:
    """Count capitalised and lowercase mentions of a headword, ignoring forced capitals.

    A mention is *sentence-initial* — and so counted on neither side — when everything
    between it and the start of the text, a newline, or a ``.``/``!``/``?`` is whitespace
    or markup (:data:`_MARKUP_PREFIX`). Capitalisation is grammatically forced in that
    position, so it carries no information about the headword itself.

    The backward scan stops at the first character that is neither whitespace nor markup,
    and mentions cannot overlap, so the whole function is ``O(len(evidence))``.

    Args:
        headword: The surface form to look for.
        evidence: The entry's own prose.

    Returns:
        ``(capitalised, lowercase)`` counts of non-sentence-initial whole-word mentions.
        A mention is "capitalised" when its first character is upper case; for a phrase
        that is the first character of the phrase, not of every word.
    """
    capitalised = lowercase = 0
    for match in _headword_pattern(headword).finditer(evidence):
        position = match.start() - 1
        while position >= 0 and evidence[position] in _MARKUP_PREFIX:
            position -= 1
        if position < 0 or evidence[position] in _SENTENCE_BREAK:
            continue
        first = match.group()[0]
        if first.isupper():
            capitalised += 1
        elif first.islower():
            lowercase += 1
    return (capitalised, lowercase)


def classify_kind_deterministic(headword: str, *, evidence: str | None = None) -> LexemeKind | None:
    """Classify a headword's :class:`LexemeKind` by rule, or give up.

    The rules, in order (``docs/SCHEMA-V3.md`` § 5, ``docs/DECISIONS.md`` D-11, D-26):

    1. a leading or trailing hyphen is an affix (``-ness``, ``pre-``);
    2. an all-caps alphabetic form of at most three letters is an abbreviation;
    3. a member of :data:`FUNCTION_WORDS` is a function word;
    4. a leading capital is a proper noun — a bare headword is never sentence-initial,
       so capitalisation carries its full signal here;
    5. for a lowercase headword with ``evidence``, how the entry's *own prose* writes the
       headword decides (D-26). Counting only mentions that are not sentence-initial —
       after ``.``, ``!``, ``?``, a newline, or at the start of the text, where a capital
       is forced by grammar and means nothing — the rule is:

       * at least :data:`_EVIDENCE_MIN_CAPITALS` capitalised mentions, outnumbering the
         lowercase ones, is a proper noun ("london", "einstein" in a store that
         lowercases every headword);
       * at least one capitalised mention, without a majority, is *undecided*: it returns
         ``None`` for the batched classifier rather than asserting a simplex;
       * no capitalised mention at all falls through to the rules below;

    6. whitespace is *ambiguous* between compound, phrasal verb and idiom, so it
       returns ``None`` and goes to the batched classifier;
    7. an internal hyphen is a compound (``mother-in-law``);
    8. anything else is a simplex.

    Args:
        headword: The surface form.
        evidence: The entry's own text — canonical glosses, encyclopedia section and
            lexical explanation, joined — or ``None`` when the caller has none. See
            :func:`entry_evidence`.

    Returns:
        The kind, or ``None`` when the rules cannot decide.
    """
    word = headword.strip()
    if not word:
        return None
    letters = word.replace(".", "")
    rules: tuple[tuple[bool, LexemeKind], ...] = (
        (word.startswith("-") or word.endswith("-"), LexemeKind.AFFIX),
        (
            letters.isalpha() and letters.isupper() and len(letters) <= _ABBREVIATION_MAX_LETTERS,
            LexemeKind.ABBREVIATION,
        ),
        (word.lower() in FUNCTION_WORDS, LexemeKind.FUNCTION_WORD),
        (word[0].isupper(), LexemeKind.PROPER_NOUN),
    )
    for matched, kind in rules:
        if matched:
            return kind
    if evidence and word == word.lower():
        capitalised, lowercase = _capitalisation_counts(word, evidence)
        if capitalised >= _EVIDENCE_MIN_CAPITALS and capitalised > lowercase:
            return LexemeKind.PROPER_NOUN
        if capitalised:
            return None
    if any(character.isspace() for character in word):
        return None
    return LexemeKind.COMPOUND if "-" in word else LexemeKind.SIMPLEX


def entry_evidence(entry: Lexeme) -> str:
    """Return an entry's own prose, for rule 5 of :func:`classify_kind_deterministic`.

    Args:
        entry: The lexeme to read.

    Returns:
        Every canonical sense gloss, the encyclopedia section and the lexical
        explanation, joined by newlines. Non-canonical renditions are left out: they are
        rewrites of the same text and would only double the counts on both sides.
    """
    parts = [sense.canonical_gloss() for _, sense, _ in entry.iter_senses()]
    for section in (entry.encyclopedia, entry.lexical_explanation):
        canonical = section.canonical()
        if canonical is not None:
            parts.append(canonical.content)
    return "\n".join(parts)


def _legacy_evidence(payload: dict[str, Any], *, pos_field: str, gloss_field: str) -> str:
    """Return a legacy payload's own prose, the raw-dict counterpart of :func:`entry_evidence`.

    Args:
        payload: The decoded legacy entry.
        pos_field: The key holding the part-of-speech entries (``"entries"`` in v1.3,
            ``"pos_entries"`` in v2.0).
        gloss_field: The per-sense definition key (``"definition"`` in v1.3, ``"gloss"``
            in v2.0).

    Returns:
        Every sense definition plus the two long-form sections, joined by newlines.
    """
    parts: list[str] = []
    for raw_entry in payload.get(pos_field) or []:
        if not isinstance(raw_entry, dict):
            continue
        for raw_sense in raw_entry.get("senses") or []:
            text = raw_sense.get(gloss_field) if isinstance(raw_sense, dict) else None
            if isinstance(text, str) and text.strip():
                parts.append(text)
    for key in ("encyclopedia_entry", "lexical_explanation"):
        text = payload.get(key)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def _kind_for_migration(
    headword: str, *, is_stopword: bool, evidence: str | None = None
) -> LexemeKind:
    """Return a kind for a migrated entry, never ``None``.

    Migration cannot leave ``kind`` unset, so an undecided headword gets a placeholder
    from its shape alone (D-12): a compound if it contains whitespace or a hyphen, a
    simplex otherwise. Migration never writes a ``classify_kind`` provenance marker, so
    the retrofit pass revisits every one of these.

    Args:
        headword: The surface form.
        is_stopword: Whether the source marked the entry as a stopword.
        evidence: The entry's own prose, for rule 5 of
            :func:`classify_kind_deterministic`.

    Returns:
        The kind to store.
    """
    if is_stopword:
        return LexemeKind.FUNCTION_WORD
    kind = classify_kind_deterministic(headword, evidence=evidence)
    if kind is not None:
        return kind
    word = headword.strip()
    ambiguous = any(character.isspace() for character in word) or "-" in word
    return LexemeKind.COMPOUND if ambiguous else LexemeKind.SIMPLEX


def _proper_noun_block(kind: LexemeKind) -> ProperNounInfo | None:
    """Return the ``proper_noun`` block a kind requires, if any.

    Neither legacy shape typed its entities, so a migrated proper noun gets
    ``entity_type=other`` for a later pass to refine.
    """
    if kind is LexemeKind.PROPER_NOUN:
        return ProperNounInfo(entity_type=EntityType.OTHER)
    return None


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _resolve_domain(text: str | None) -> tuple[DomainTag | None, str | None]:
    """Map a legacy free-text domain to a tag, or keep it as a hint.

    A string that is exactly one of the taxonomy roots resolves to that root's
    ``.general`` leaf, which covers the v1.3 ``domain:<root>`` tags ("language",
    "arts") that predate the legacy map.

    Args:
        text: The legacy domain string, if the source had one.

    Returns:
        ``(domain, domain_hint)``; at most one is non-``None``.
    """
    if not text or not text.strip():
        return (None, None)
    mapped = legacy_domain(text)
    if mapped is not None:
        return (mapped, None)
    normalized = "_".join(text.split()).lower()
    if normalized in ROOTS:
        return (DomainTag(f"{normalized}.general"), None)
    return (None, text.strip())


def _example_renditions(texts: list[str], headword: str, forms: list[str]) -> Renditions[Example]:
    """Turn plain example strings into canonical example renditions with spans.

    Args:
        texts: The example sentences, in source order.
        headword: The entry's headword, for span finding.
        forms: Inflected forms to try when the bare headword does not occur.

    Returns:
        A :class:`Renditions` set; duplicate example texts are dropped, since the
        uniqueness key for examples includes the text.
    """
    renditions = Renditions[Example](root=[])
    for text in texts:
        cleaned = text.strip()
        if not cleaned:
            continue
        example = Example(text=cleaned, span=find_span(cleaned, headword, forms))
        rendition = canonical_rendition(example)
        if any(r.content.text == cleaned and r.is_canonical for r in renditions):
            continue
        renditions.add(rendition)
    return renditions


def _relations_from_lists(buckets: list[tuple[RelationType, list[str]]]) -> list[Relation]:
    """Flatten v1.x/v2 parallel relation lists into one typed relation list.

    Targets are unresolved: ``sense_id`` and ``confidence`` stay ``None`` until the
    ``resolve`` stage runs.

    Args:
        buckets: ``(relation_type, terms)`` pairs in the order they should appear.

    Returns:
        The relations, de-duplicated on ``(type, term)`` and in bucket order.
    """
    relations: list[Relation] = []
    seen: set[tuple[RelationType, str]] = set()
    for relation_type, terms in buckets:
        for term in terms:
            cleaned = (term or "").strip()
            if not cleaned:
                continue
            try:
                slugify(cleaned)
            except ValueError:
                continue
            key = (relation_type, cleaned.lower())
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                Relation(type=relation_type, target=RelationTarget(term=cleaned)),
            )
    return relations


def _provenance(record: dict[str, Any]) -> Provenance:
    """Validate a legacy provenance record, renaming stages v3 has renamed.

    Args:
        record: The raw provenance object.

    Returns:
        The validated :class:`Provenance`.
    """
    stage = record.get("stage")
    if isinstance(stage, str) and stage in _LEGACY_STAGES:
        record = {**record, "stage": _LEGACY_STAGES[stage]}
    return Provenance.model_validate(record)


def _parse_timestamp(value: Any) -> dt.datetime:  # noqa: ANN401 - raw JSON value
    """Parse a legacy timestamp, defaulting to now and always timezone-aware."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return dt.datetime.now(tz=dt.UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    return dt.datetime.now(tz=dt.UTC)


def _first(values: Any) -> str | None:  # noqa: ANN401 - raw JSON value
    """Return the first non-empty string of a legacy list-valued morphology field."""
    if isinstance(values, str):
        return values or None
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


# --------------------------------------------------------------------------------------
# v2.0 -> v3.0
# --------------------------------------------------------------------------------------

_V2_RELATION_FIELDS: tuple[tuple[RelationType, str], ...] = (
    (RelationType.SYNONYM, "synonyms"),
    (RelationType.ANTONYM, "antonyms"),
    (RelationType.HYPERNYM, "hypernyms"),
    (RelationType.HYPONYM, "hyponyms"),
    (RelationType.MERONYM, "meronyms"),
    (RelationType.HOLONYM, "holonyms"),
)


def from_v2(payload: dict[str, Any]) -> Lexeme:
    """Upgrade a v2.0 payload to v3.0.

    The six parallel relation lists become one typed ``relations`` list with unresolved
    targets; ``gloss`` becomes the canonical gloss rendition and ``variants`` become the
    rest of that set; ``examples`` become canonical example renditions with spans filled
    deterministically; the provenance list becomes the keyed provenance table.

    Args:
        payload: A decoded v2.0 entry.

    Returns:
        The validated v3 entry.
    """
    headword = str(payload["headword"])
    is_stopword = bool(payload.get("is_stopword", False))
    kind = _kind_for_migration(
        headword,
        is_stopword=is_stopword,
        evidence=_legacy_evidence(payload, pos_field="pos_entries", gloss_field="gloss"),
    )
    entry_domain = payload.get("domain")

    entry = Lexeme(
        lexeme_id=slugify(headword),
        headword=headword,
        language=str(payload.get("language", "en")),
        kind=kind,
        proper_noun=_proper_noun_block(kind),
        status=EntryStatus(payload.get("status", EntryStatus.COMPLETE.value)),
        is_stopword=is_stopword,
        frequency=payload.get("frequency"),
        discovered_from=payload.get("discovered_from"),
        created_at=_parse_timestamp(payload.get("created_at")),
        updated_at=_parse_timestamp(payload.get("updated_at")),
    )
    for record in payload.get("provenance") or []:
        entry.add_provenance(_provenance(record))

    for raw_entry in payload.get("pos_entries") or []:
        entry.pos_entries.append(_v2_pos_entry(raw_entry, entry, headword, entry_domain))

    etymology = payload.get("etymology")
    if etymology:
        entry.etymology = Etymology.model_validate(etymology)
    encyclopedia = payload.get("encyclopedia_entry")
    if isinstance(encyclopedia, str) and encyclopedia.strip():
        entry.encyclopedia.add(canonical_rendition(encyclopedia))
    explanation = payload.get("lexical_explanation")
    if isinstance(explanation, str) and explanation.strip():
        entry.lexical_explanation.add(canonical_rendition(explanation))
    return Lexeme.model_validate(entry.model_dump(mode="json"))


def _v2_pos_entry(
    raw: dict[str, Any], entry: Lexeme, headword: str, entry_domain: str | None
) -> POSEntry:
    """Build one v3 :class:`POSEntry` from a v2 part-of-speech entry."""
    morphology = Morphology.model_validate(raw.get("morphology") or {})
    forms = morphology.inflected_forms()
    senses = [
        _v2_sense(raw_sense, entry, headword, forms, entry_domain)
        for raw_sense in raw.get("senses") or []
    ]
    return POSEntry(
        pos=PartOfSpeech(raw["pos"]),
        senses=senses,
        morphology=morphology,
        collocations=list(raw.get("collocations") or []),
    )


def _v2_sense(
    raw: dict[str, Any],
    entry: Lexeme,
    headword: str,
    forms: list[str],
    entry_domain: str | None,
) -> Sense:
    """Build one v3 :class:`Sense` from a v2 sense, keeping its index."""
    gloss = Renditions[str](root=[canonical_rendition(str(raw["gloss"]))])
    for raw_variant in raw.get("variants") or []:
        rendition = _v2_variant(raw_variant, entry)
        if gloss.has(rendition.reading_level, rendition.style):
            continue
        gloss.add(rendition)

    domain, hint = _resolve_domain(raw.get("domain") or entry_domain)
    return Sense(
        index=int(raw["index"]),
        gloss=gloss,
        examples=_example_renditions(list(raw.get("examples") or []), headword, forms),
        relations=_relations_from_lists(
            [(kind, list(raw.get(field) or [])) for kind, field in _V2_RELATION_FIELDS]
        ),
        domain=domain,
        domain_hint=hint,
        retired=bool(raw.get("retired", False)),
    )


def _v2_variant(raw: dict[str, Any], entry: Lexeme) -> Rendition[str]:
    """Convert one v2 ``DefinitionVariant`` into a gloss rendition.

    ``measured_grade_level`` becomes an :class:`Assessment`; an inline ``provenance``
    record is moved into the entry's provenance table and referenced by id.
    """
    provenance_id: str | None = None
    record = raw.get("provenance")
    if record:
        provenance_id = entry.add_provenance(_provenance(record))
    grade = raw.get("measured_grade_level")
    return Rendition[str](
        reading_level=ReadingLevel(raw["reading_level"]),
        style=Register(raw.get("register") or raw["style"]),
        content=str(raw["text"]),
        provenance_id=provenance_id,
        assessment=Assessment(readability_grade=grade) if grade is not None else None,
    )


# --------------------------------------------------------------------------------------
# v1.3 -> v3.0
# --------------------------------------------------------------------------------------

_V13_RELATION_FIELDS: tuple[tuple[RelationType, str], ...] = (
    (RelationType.SYNONYM, "synonyms"),
    (RelationType.ANTONYM, "antonyms"),
    (RelationType.HYPERNYM, "hypernyms"),
    (RelationType.HYPONYM, "hyponyms"),
)


def from_v13(payload: dict[str, Any]) -> Lexeme:
    """Upgrade a v1.3 working-store payload to v3.0.

    ``word`` becomes the headword; every ``uuid4`` and the materialised ``edges`` list
    are dropped, because v3 derives both; ``wiki_frequency`` becomes ``frequency``,
    tagged with ``frequency_corpus`` (see :data:`_V13_FREQUENCY_CORPUS`; A3), and
    ``stopword.is_stopword`` becomes ``is_stopword`` (and ``kind=function_word``).
    Domain tags of the form ``domain:<name>`` are mapped through the taxonomy's legacy
    map onto every sense of the entry, since v1.3 tagged entries, not senses.

    Args:
        payload: A decoded v1.3 entry.

    Returns:
        The validated v3 entry.
    """
    headword = str(payload["word"])
    stopword = payload.get("stopword") or {}
    is_stopword = bool(stopword.get("is_stopword", False))
    kind = _kind_for_migration(
        headword,
        is_stopword=is_stopword,
        evidence=_legacy_evidence(payload, pos_field="entries", gloss_field="definition"),
    )
    domain, secondary, hint = _v13_domains(payload.get("tags") or [])

    entry = Lexeme(
        lexeme_id=slugify(headword),
        headword=headword,
        language=str(payload.get("language") or "en"),
        kind=kind,
        proper_noun=_proper_noun_block(kind),
        status=EntryStatus.COMPLETE,
        is_stopword=is_stopword,
        frequency=_v13_frequency(payload.get("wiki_frequency")),
        frequency_corpus=_V13_FREQUENCY_CORPUS,
        created_at=_parse_timestamp(payload.get("created_at")),
        updated_at=_parse_timestamp(payload.get("updated_at")),
    )

    seen_pos: set[PartOfSpeech] = set()
    for raw_entry in payload.get("entries") or []:
        pos = _v13_pos(raw_entry.get("pos"))
        if pos is None or pos in seen_pos:
            continue
        seen_pos.add(pos)
        entry.pos_entries.append(
            _v13_pos_entry(raw_entry, pos, headword, domain=domain, secondary=secondary, hint=hint)
        )

    etymology = _v13_etymology(payload.get("etymology"))
    if etymology is not None:
        entry.etymology = etymology
    encyclopedia = payload.get("encyclopedia_entry")
    if isinstance(encyclopedia, str) and encyclopedia.strip():
        entry.encyclopedia.add(canonical_rendition(encyclopedia))
    explanation = payload.get("lexical_explanation")
    if isinstance(explanation, str) and explanation.strip():
        entry.lexical_explanation.add(canonical_rendition(explanation))
    return Lexeme.model_validate(entry.model_dump(mode="json"))


def _v13_frequency(value: Any) -> float | None:  # noqa: ANN401 - raw JSON value
    """Return ``wiki_frequency`` as a float, or ``None`` when absent."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _v13_pos(value: Any) -> PartOfSpeech | None:  # noqa: ANN401 - raw JSON value
    """Map a v1.3 ``pos`` string onto :class:`PartOfSpeech`, or ``None`` if unknown."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in _V13_SKIP_POS:
        return None
    try:
        return PartOfSpeech(normalized)
    except ValueError:
        return None


def _v13_domains(tags: list[Any]) -> tuple[DomainTag | None, list[DomainTag], str | None]:
    """Split v1.3 ``domain:<name>`` tags into a primary tag, secondaries, and a hint.

    Args:
        tags: The entry's raw ``tags`` list.

    Returns:
        ``(domain, secondary_domains, domain_hint)``. The hint holds the first
        unmappable domain string, so the ``tag_domain`` stage still sees it.
    """
    resolved: list[DomainTag] = []
    hint: str | None = None
    for tag in tags:
        if not isinstance(tag, str) or not tag.startswith("domain:"):
            continue
        mapped, unmapped = _resolve_domain(tag.removeprefix("domain:"))
        if mapped is not None and mapped not in resolved:
            resolved.append(mapped)
        elif unmapped is not None and hint is None:
            hint = unmapped
    if not resolved:
        return (None, [], hint)
    return (resolved[0], resolved[1:], hint)


def _v13_pos_entry(
    raw: dict[str, Any],
    pos: PartOfSpeech,
    headword: str,
    *,
    domain: DomainTag | None,
    secondary: list[DomainTag],
    hint: str | None,
) -> POSEntry:
    """Build one v3 :class:`POSEntry` from a v1.3 entry, preserving sense order."""
    morphology = _v13_morphology(raw.get("morphology"))
    forms = morphology.inflected_forms()
    base_form = ((raw.get("morphology") or {}).get("base_form") or "").strip()
    if base_form and base_form != headword:
        forms = [base_form, *forms]

    senses: list[Sense] = []
    for raw_sense in raw.get("senses") or []:
        definition = str(raw_sense.get("definition") or "").strip()
        if not definition:
            continue
        senses.append(
            Sense(
                index=len(senses),
                gloss=Renditions[str](root=[canonical_rendition(definition)]),
                examples=_example_renditions(
                    list(raw_sense.get("examples") or []), headword, forms
                ),
                relations=_relations_from_lists(
                    [
                        (kind, list(raw_sense.get(field) or []))
                        for kind, field in _V13_RELATION_FIELDS
                    ]
                ),
                domain=domain,
                secondary_domains=list(secondary),
                domain_hint=hint,
            )
        )
    return POSEntry(
        pos=pos,
        senses=senses,
        morphology=morphology,
        collocations=[c for c in (raw.get("collocations") or []) if isinstance(c, str)],
    )


def _v13_morphology(raw: dict[str, Any] | None) -> Morphology:
    """Flatten v1.3's list-valued inflections and grouped derivations onto v3's shape."""
    if not raw:
        return Morphology()
    inflections = raw.get("inflections") or {}
    derivations_block = raw.get("derivations") or {}
    derivations: list[str] = []
    for group in ("noun_forms", "verb_forms", "adjective_forms", "adverb_forms"):
        for value in derivations_block.get(group) or []:
            if isinstance(value, str) and value.strip() and value not in derivations:
                derivations.append(value.strip())
    return Morphology(
        plural=_first(inflections.get("plural")),
        past_tense=_first(inflections.get("past_tense")),
        past_participle=_first(inflections.get("past_participle")),
        present_participle=_first(inflections.get("present_participle")),
        third_person_singular=_first(inflections.get("third_person_singular")),
        comparative=_first(inflections.get("comparative")),
        superlative=_first(inflections.get("superlative")),
        derivations=derivations,
    )


def _v13_etymology(raw: dict[str, Any] | None) -> Etymology | None:
    """Convert a v1.3 etymology block, whose segments use ``headword``/``gloss``."""
    if not raw or not str(raw.get("summary") or "").strip():
        return None
    segments = [
        EtymologySegment(
            language=str(segment.get("language") or "unknown"),
            form=str(segment.get("headword") or segment.get("form") or ""),
            meaning=segment.get("gloss") or segment.get("meaning"),
            era=segment.get("era"),
        )
        for segment in raw.get("segments") or []
        if str(segment.get("headword") or segment.get("form") or "").strip()
    ]
    return Etymology(
        summary=str(raw["summary"]),
        segments=segments,
        cognates=[c for c in (raw.get("cognates") or []) if isinstance(c, str)],
        references=[r for r in (raw.get("references") or []) if isinstance(r, str)],
    )
