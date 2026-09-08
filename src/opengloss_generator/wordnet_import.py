"""Build schema-v3 entries from Princeton WordNet 3.0.

This is a third *source* for the store, beside the two legacy shapes ``migrate.py``
upgrades. It is deliberately a sibling of that module rather than a third branch inside
it: a v1.3 or v2.0 payload is a *document to upgrade*, whereas WordNet is a *corpus to
read* — there is no payload, the input is a word list, and the whole job is the mapping
table below. See D-78.

Not to be confused with :mod:`opengloss_generator.wordnet` (D-79), which asks WordNet
*yes/no questions about the store* — "is this plural a lemma in its own right?" — and
answers ``None`` when the corpus is missing, because a free signal may legitimately be
absent. This module *reads WordNet into the store*, so a missing corpus is not a skipped
check but a command that cannot run, and :func:`load_wordnet` raises. The two share the
one thing worth sharing: :func:`opengloss_generator.wordnet.normalise`, the single place a
project string becomes a WordNet lemma key, and ``availability()``, the single place the
corpus is probed for.

The same three rules `migrate.py` states apply here:

1. **Senses are never renumbered.** A sense's id is its position (D-1), so senses are
   written in WordNet's own sense order and numbered ``0..n-1`` in that order.
2. **Nothing random is carried.** WordNet's synset offsets and sense keys are not stored;
   every v3 id is derived from the headword, the part of speech and the sense index.
3. **Nothing is invented.** A WordNet lexname that does not map onto a *specific*
   taxonomy leaf leaves ``domain`` unset and lands in ``domain_hint``, for the
   ``tag_domain`` stage to answer (D-44). Morphology beyond WordNet's own
   derivationally-related forms, etymology, encyclopedia text, levelled renditions and
   example spans are all left for the enrichment chain, exactly as they are for a
   migrated v1.3 entry.

Unlike the two migrations, every inherited field here carries a ``migrate`` provenance
record naming WordNet as its source, so a reader of a stored entry can tell which text
this package generated and which it copied (D-78).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opengloss_generator.errors import WordNetUnavailableError
from opengloss_generator.identity import slugify
from opengloss_generator.migrate import FUNCTION_WORDS, relations_from_lists
from opengloss_generator.schema import (
    EntityType,
    EntryStatus,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Provenance,
    Relation,
    RelationType,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.taxonomy import DomainTag
from opengloss_generator.wordnet import availability, normalise

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opengloss_generator.schema import Rendition

__all__ = [
    "DEFAULT_CANDIDATES_PATH",
    "LEXNAME_DOMAIN_MAP",
    "LEXNAME_ENTITY_MAP",
    "POS_LETTERS",
    "PROVENANCE_FIELDS",
    "WORDNET_LICENSE",
    "WORDNET_MODEL",
    "WORDNET_NOTE",
    "WORDNET_PROMPT_VERSION",
    "WORDNET_VERSION",
    "CandidateRow",
    "candidate_index",
    "clean_definition",
    "clean_examples",
    "entity_type_for_lexnames",
    "entry_for",
    "iter_entries",
    "lexname_domain",
    "load_wordnet",
    "read_candidate_rows",
    "read_candidates",
    "wordnet_kind",
]

#: The release this importer reads. NLTK ships Princeton WordNet 3.0; the value is
#: cross-checked against ``corpus.get_version()`` at load time so a future NLTK data
#: bundle cannot silently change what an entry claims as its source.
WORDNET_VERSION = "3.0"

#: ``Provenance.model`` for every record this module writes. It is the flag downstream
#: code (dataset cards, licence notices) tests for: an entry carrying a provenance record
#: with this model is WordNet-derived and inherits the Princeton licence.
WORDNET_MODEL = f"wordnet-{WORDNET_VERSION}"

#: The licence WordNet-sourced content is distributed under. ``LICENSES/WordNet.txt``
#: holds the full text.
WORDNET_LICENSE = "Princeton WordNet License"

#: ``Provenance.note`` for every record this module writes, prefixed by the name of the
#: field the record accounts for (see :data:`PROVENANCE_FIELDS`). The prefix is what makes
#: "one record per inherited field" observable on a stored entry; the sentence after it is
#: identical in every record and is what a licence audit greps for.
WORDNET_NOTE = f"imported from WordNet {WORDNET_VERSION} ({WORDNET_LICENSE})"

#: ``Provenance.prompt_version`` for a record that answers for no prompt at all. This
#: import makes no model call, so there is no prompt to version; the field is required, so
#: it names the mapping instead — bump it when the mapping below changes shape.
WORDNET_PROMPT_VERSION = "wordnet-import-1"

#: The inherited fields that get a provenance record, in the order the records are added
#: (so ``p1`` is always the gloss record on an entry that has senses). A field with no
#: content on a given entry gets no record: an empty ``examples`` record would assert that
#: WordNet supplied examples it did not supply.
PROVENANCE_FIELDS: tuple[str, ...] = ("gloss", "examples", "relations", "morphology", "domain")

#: WordNet's four part-of-speech letters, in the order part-of-speech entries are written.
#: ``a`` covers both head adjectives and satellites: WordNet's own ``index.adj`` lists them
#: under one part of speech and in one sense order, and ``synsets(word, "a")`` returns that
#: order, so querying ``s`` separately would both duplicate senses and reorder them.
POS_LETTERS: tuple[tuple[str, PartOfSpeech], ...] = (
    ("n", PartOfSpeech.NOUN),
    ("v", PartOfSpeech.VERB),
    ("a", PartOfSpeech.ADJECTIVE),
    ("r", PartOfSpeech.ADVERB),
)

#: Synset-level pointer methods, grouped onto the v3 relation type each becomes, in the
#: order relations are written onto a sense. Notes on the four groupings that are not
#: one-to-one:
#:
#: * ``similar_tos`` (an adjective satellite's head, and the satellites of a head) is a
#:   synonym edge, and joins the *other lemmas of the same synset*, which are synonyms by
#:   construction and are added first.
#: * ``instance_hypernyms`` becomes ``instance_of`` rather than ``hypernym``: that member
#:   exists for exactly this pointer, and ``schema.WN_RELATION_MAP`` already exports it
#:   as WN-LMF's ``instance_hypernym``. Its inverse, ``instance_hyponyms``, has no
#:   dedicated member and joins ``hyponym``.
#: * The three meronym pointers (part / member / substance) and the three holonym
#:   pointers collapse onto one relation type each: v3 types the *direction* of a
#:   part-whole edge, not the flavour of part-hood (``schema.WN_RELATION_MAP`` says the
#:   same thing on the export side).
#:
#: Deliberately absent: ``entailments`` and ``causes`` — v3 has ``entails`` and ``causes``
#: and could carry them, but this first import ships the reviewed list only — and
#: ``verb_groups``, which is a sense-grouping device rather than an assertion about
#: meaning.
SYNSET_RELATIONS: tuple[tuple[RelationType, tuple[str, ...]], ...] = (
    (RelationType.SYNONYM, ("similar_tos",)),
    # No synset-level pointer: WordNet records antonymy between word *forms*, so a
    # sense's antonyms are read off this headword's own lemmas in `_relations_for`
    # ("good"/"bad" are antonyms; "good"/"evilness" are not). The row is here so the
    # table is the whole mapping and so it fixes where antonyms sit in the written order.
    (RelationType.ANTONYM, ()),
    (RelationType.HYPERNYM, ("hypernyms",)),
    (RelationType.INSTANCE_OF, ("instance_hypernyms",)),
    (RelationType.HYPONYM, ("hyponyms", "instance_hyponyms")),
    (RelationType.MERONYM, ("part_meronyms", "member_meronyms", "substance_meronyms")),
    (RelationType.HOLONYM, ("part_holonyms", "member_holonyms", "substance_holonyms")),
    (RelationType.SEE_ALSO, ("also_sees",)),
)

#: WordNet lexname (supersense) -> taxonomy leaf, for the lexnames whose mapping is not a
#: judgement call. Two rules bound this table, and both matter more than its size:
#:
#: * **A lexname maps only onto a *specific* leaf, never onto a root's ``.general``
#:   catch-all.** D-44 exists because 84% of v1.3 landed in one general bucket; a table
#:   that swept ``noun.artifact``'s 11,587 synsets into ``everyday_life.general`` would
#:   recreate that failure for free. An unmapped lexname costs one ``tag_domain`` call and
#:   gets a real answer.
#: * **A lexname maps only where the whole supersense fits the leaf.** ``noun.person``
#:   holds chemists and quarterbacks; ``noun.substance`` holds both chemicals and cheeses;
#:   ``noun.communication`` is far wider than "conversation". All three are left to
#:   ``tag_domain``, which sees the gloss and can tell them apart.
#:
#: ``noun.location`` -> ``nature.landforms`` follows the precedent already set in
#: ``taxonomy.LEGACY_DOMAIN_MAP``, where the v1.3 free-text domain "geography" resolves to
#: the same leaf.
LEXNAME_DOMAIN_MAP: dict[str, DomainTag] = {
    "noun.animal": DomainTag.NATURE_ANIMALS,
    "noun.plant": DomainTag.NATURE_PLANTS,
    "noun.body": DomainTag.HEALTH_ANATOMY,
    "noun.food": DomainTag.EVERYDAY_LIFE_FOOD,
    "noun.feeling": DomainTag.PEOPLE_SOCIETY_EMOTION_ATTITUDE,
    "noun.shape": DomainTag.MATHEMATICS_GEOMETRY,
    "noun.location": DomainTag.NATURE_LANDFORMS,
    "noun.time": DomainTag.EVERYDAY_LIFE_QUANTITY_TIME,
    "noun.quantity": DomainTag.EVERYDAY_LIFE_QUANTITY_TIME,
    "verb.emotion": DomainTag.PEOPLE_SOCIETY_EMOTION_ATTITUDE,
    "verb.weather": DomainTag.NATURE_WEATHER,
    "verb.motion": DomainTag.EVERYDAY_LIFE_ACTIONS_ROUTINES,
    "verb.consumption": DomainTag.EVERYDAY_LIFE_FOOD,
    "verb.communication": DomainTag.LANGUAGE_COMMUNICATION,
}

#: The candidate file's ``source`` column value that marks a row as WordNet's.
WORDNET_SOURCE = "wordnet"

#: The tier-6 candidate list ``entity_type`` is read from when a caller names no other
#: file (D-81). Gitignored like every other tier list, so an absent file is not an error:
#: the readers below return nothing and every proper noun falls through to the model.
DEFAULT_CANDIDATES_PATH = Path("data/core/tier6_candidates.tsv")

#: WordNet lexname of an **instance hypernym** -> the entity type it implies (D-81).
#: WordNet's encoding of "this is a named individual" is an ``instance_hypernym`` pointer
#: (*Abraham Lincoln* is an instance of *President of the United States*), and the class
#: it points at is filed under a supersense that already answers "what kind of thing":
#: ``noun.person`` for a person, ``noun.location`` for a place, ``noun.group`` for an
#: organization. This is the free half of the entity typing — an instance synset carries
#: it whether or not a candidate list names the word.
#:
#: The table is deliberately short. ``noun.artifact`` and ``noun.communication`` map to
#: ``work`` because the instance hypernyms actually reached through them are works —
#: a painting, a ship, a poem, a language's named text — not because either supersense is
#: about creative works in general; a supersense whose instances are not all one type is
#: absent, and its entry falls through to :class:`~opengloss_generator.schema.EntityType`'s
#: own placeholder for the ``entity_type`` retrofit pass to buy an answer for.
LEXNAME_ENTITY_MAP: dict[str, EntityType] = {
    "noun.person": EntityType.PERSON,
    "noun.location": EntityType.PLACE,
    "noun.group": EntityType.ORGANIZATION,
    "noun.communication": EntityType.WORK,
    "noun.artifact": EntityType.WORK,
    "noun.act": EntityType.EVENT,
    "noun.event": EntityType.EVENT,
}

#: The order :func:`entity_type_for_lexnames` resolves a tie in. A synset can have more
#: than one instance hypernym (``Washington`` is an instance of both a *statesman* and a
#: *national capital*), so "first match wins" needs a fixed order or the answer depends on
#: WordNet's pointer order. Person first because a named individual is the commonest case
#: and the least ambiguous; ``other``-ish supersenses last.
_ENTITY_PRECEDENCE: tuple[EntityType, ...] = (
    EntityType.PERSON,
    EntityType.PLACE,
    EntityType.ORGANIZATION,
    EntityType.EVENT,
    EntityType.WORK,
)


def entity_type_for_lexnames(lexnames: Iterable[str]) -> EntityType | None:
    """Return the entity type a set of instance-hypernym lexnames implies, or ``None``.

    Args:
        lexnames: ``Synset.lexname()`` of every instance hypernym of every synset the
            headword has, in any order.

    Returns:
        The highest-precedence type any of them maps to (:data:`_ENTITY_PRECEDENCE`), or
        ``None`` when :data:`LEXNAME_ENTITY_MAP` covers none of them — which is the
        honest answer, not :attr:`~opengloss_generator.schema.EntityType.OTHER`.
    """
    found = {
        mapped
        for lexname in lexnames
        if (mapped := LEXNAME_ENTITY_MAP.get(lexname.strip().lower())) is not None
    }
    return next((candidate for candidate in _ENTITY_PRECEDENCE if candidate in found), None)


#: What to do about each reason :func:`opengloss_generator.wordnet.availability` can give.
#: Keyed on its exact strings, so a new reason there fails loudly here rather than silently
#: producing an error message with no instruction in it.
_INSTALL_HINTS: dict[str, str] = {
    # The fallback, for an `Availability` that reports unusable without saying why.
    "WordNet is unavailable": (
        "see `uv sync --extra wordnet` and `python -m nltk.downloader wordnet`"
    ),
    "nltk not installed": (
        "install the optional extra with `uv sync --extra wordnet` "
        "(or `pip install 'opengloss-generator[wordnet]'`)"
    ),
    "wordnet corpus not downloaded": "run `python -m nltk.downloader wordnet`",
}


def load_wordnet() -> Any:  # noqa: ANN401 - the NLTK corpus reader is untyped
    """Return NLTK's WordNet corpus reader.

    ``nltk`` is an optional dependency and the corpus itself is a separate download.
    :func:`opengloss_generator.wordnet.availability` already distinguishes those two and
    caches the loaded reader, so this adds only what an *importer* needs on top of a free
    signal's probe: an exception rather than a ``None``, an instruction for each of the two
    causes, and a check that the corpus really is release 3.0 — the lexname mapping and the
    sense ordering below are pinned to it.

    Returns:
        The ``nltk.corpus.wordnet`` reader, with its data files already open.

    Raises:
        WordNetUnavailableError: If ``nltk`` is not installed, if the ``wordnet`` corpus
            has not been downloaded, or if the installed corpus is not the release
            :data:`WORDNET_VERSION` names.
    """
    status = availability()
    if not status.usable:
        reason = status.reason or "WordNet is unavailable"
        raise WordNetUnavailableError(f"{reason}: {_INSTALL_HINTS[reason]}")
    from nltk.corpus import wordnet  # noqa: PLC0415 - optional dependency, probed above

    version = wordnet.get_version()
    if version != WORDNET_VERSION:
        raise WordNetUnavailableError(
            f"expected WordNet {WORDNET_VERSION}, found {version!r}: this importer's "
            "lexname mapping and sense ordering are pinned to 3.0"
        )
    return wordnet


def read_candidates(path: Path, *, source: str | None = WORDNET_SOURCE) -> list[str]:
    """Read the ``word`` column of a tier-candidate TSV, optionally filtered by source.

    ``data/core/tier5_candidates.tsv`` mixes rows this importer owns with rows the v1.3
    migration owns, distinguished by a ``source`` column. Reading it with the CLI's
    generic ``_read_word_list`` would hand the v1.3 words to WordNet as well.

    Args:
        path: The TSV to read. A header row naming ``word`` is required; a ``source``
            column is required only when ``source`` is given.
        source: Keep only rows whose ``source`` cell equals this. ``None`` keeps every
            row, for a plain word list with no ``source`` column.

    Returns:
        The words, in file order, with blank cells dropped and duplicates removed.

    Raises:
        ValueError: If the file has no header row, no ``word`` column, or no ``source``
            column when one was asked for.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().lower() for cell in lines[0].split("\t")]
    if "word" not in header:
        raise ValueError(f"{path} has no 'word' column in its header row")
    word_at = header.index("word")
    source_at: int | None = None
    if source is not None:
        if "source" not in header:
            raise ValueError(f"{path} has no 'source' column, so --source {source!r} cannot apply")
        source_at = header.index("source")

    words: list[str] = []
    seen: set[str] = set()
    for line in lines[1:]:
        cells = line.split("\t")
        if word_at >= len(cells):
            continue
        if source_at is not None and (
            source_at >= len(cells) or cells[source_at].strip() != source
        ):
            continue
        word = cells[word_at].strip()
        if not word or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One row of a tier-candidate TSV, as much of it as the store can store (D-81).

    A candidate list is the only place the entity type and the Wikidata QID of a name are
    known *before* a model is asked, so reading them here is what stops the import path
    writing :attr:`~opengloss_generator.schema.EntityType.OTHER` over an answer a source
    already gave (D-12/D-18's placeholder, NAMED-ENTITY-PLAN § 4a-b).

    Attributes:
        word: The ``word`` cell, the surface form to import or look up.
        lexeme_id: ``slugify(word)`` — the key every store lookup uses.
        entity_type: The row's ``entity_type`` cell, when it names an
            :class:`~opengloss_generator.schema.EntityType` member. A cell naming
            something outside the enum is dropped rather than coerced, so a widened
            candidate builder cannot smuggle an out-of-vocabulary type into the store.
        wikidata_qid: The row's ``qid`` cell when it is a well-formed QID.
        alias_target: The lexeme id named by an ``alias_of candidate`` note, when the row
            carries one — the single-word entry the store already holds for part of this
            name ("lincoln" for "Abraham Lincoln").
    """

    word: str
    lexeme_id: str
    entity_type: EntityType | None = None
    wikidata_qid: str | None = None
    alias_target: str | None = None


#: The shape of the ``notes`` cell that records an alias opportunity, as
#: ``scripts/build_tier6_candidates.py`` writes it: ``alias_of candidate: store has
#: 'lincoln'``. The quoted id is what the alias step links to.
_ALIAS_NOTE = re.compile(r"alias_of candidate:[^;]*?'([^']+)'")

#: A well-formed Wikidata item id, the same pattern
#: :class:`~opengloss_generator.schema.ProperNounInfo` validates against. Matched here so
#: a malformed cell is dropped at read time rather than raising deep inside entry
#: construction.
_QID = re.compile(r"^Q[1-9][0-9]*$")


def read_candidate_rows(path: Path, *, source: str | None = None) -> list[CandidateRow]:
    """Read a tier-candidate TSV into typed rows, header-driven and source-filtered.

    :func:`read_candidates` reads the same file for the one column ``import-wordnet``
    needs; this reads the columns the *entity typing* and *alias* passes need, from the
    same header-driven, ``source``-filtered shape, so the two never disagree about which
    rows belong to whom. Every column but ``word`` is optional: a plain word list yields
    rows carrying nothing but the word, which is exactly what a caller with no candidate
    file should see.

    Args:
        path: The TSV to read. A header row naming ``word`` is required. A missing file
            yields no rows at all — the tier lists are gitignored (D-75), so their absence
            is an ordinary state of the tree and not an error.
        source: Keep only rows whose ``source`` cell equals this. ``None`` keeps every
            row.

    Returns:
        The rows, in file order, one per distinct lexeme id — the first row for an id
        wins, matching :func:`read_candidates`' own de-duplication.

    Raises:
        ValueError: If the file has a header but no ``word`` column, or no ``source``
            column when one was asked for.
    """
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().lower() for cell in lines[0].split("\t")]
    if "word" not in header:
        raise ValueError(f"{path} has no 'word' column in its header row")
    if source is not None and "source" not in header:
        raise ValueError(f"{path} has no 'source' column, so --source {source!r} cannot apply")
    index = {name: position for position, name in enumerate(header)}

    def cell(cells: Sequence[str], name: str) -> str:
        position = index.get(name)
        if position is None or position >= len(cells):
            return ""
        return cells[position].strip()

    rows: list[CandidateRow] = []
    seen: set[str] = set()
    for line in lines[1:]:
        cells = line.split("\t")
        word = cell(cells, "word")
        if not word:
            continue
        if source is not None and cell(cells, "source") != source:
            continue
        lexeme_id = slugify(word)
        if lexeme_id in seen:
            continue
        seen.add(lexeme_id)
        raw_type = cell(cells, "entity_type").lower()
        qid = cell(cells, "qid")
        note = _ALIAS_NOTE.search(cell(cells, "notes"))
        rows.append(
            CandidateRow(
                word=word,
                lexeme_id=lexeme_id,
                entity_type=EntityType(raw_type) if raw_type in _ENTITY_VALUES else None,
                wikidata_qid=qid if _QID.match(qid) else None,
                alias_target=note.group(1) if note else None,
            )
        )
    return rows


#: Every :class:`~opengloss_generator.schema.EntityType` value, for the membership test
#: above — ``EntityType(x)`` raises on an unknown string and a candidate list is an
#: outside file, so the test comes before the construction.
_ENTITY_VALUES: frozenset[str] = frozenset(member.value for member in EntityType)


def candidate_index(path: Path, *, source: str | None = None) -> dict[str, CandidateRow]:
    """Return :func:`read_candidate_rows` keyed by lexeme id, for a store-side lookup.

    Args:
        path: The candidate TSV.
        source: Optional ``source`` filter.

    Returns:
        ``lexeme_id -> row``. Empty when the file is absent.
    """
    return {row.lexeme_id: row for row in read_candidate_rows(path, source=source)}


# --------------------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------------------


def clean_definition(text: str) -> str:
    """Return a synset definition with WordNet's own leaked example fragments removed.

    NLTK splits a synset's gloss into ``definition()`` and ``examples()`` on the source
    file's quoting, and for 20 of WordNet 3.0's 117,659 synsets that quoting is broken, so
    the definition keeps a fragment of an example. ``compound.a.01`` reads ``composed of
    more than one part; compound flower heads"`` and ``flimsy.s.02`` reads ``lacking
    substance or significance; ; ; ; a fragile claim to fame"``.

    Both shapes are recognisable without touching a correct definition: a leaked fragment
    is either introduced by an *empty* semicolon-separated segment (the one whose quoted
    text went to ``examples()``) or carries a stray double quote of its own. So the
    definition is kept up to, and not including, the first segment that is empty or
    quoted. A well-formed definition has neither — its semicolons all separate real
    clauses — and comes back unchanged.

    Args:
        text: ``Synset.definition()``.

    Returns:
        The cleaned definition, whitespace-normalised and without a trailing separator.
    """
    kept: list[str] = []
    for segment in text.split(";"):
        if not segment.strip() or '"' in segment:
            break
        kept.append(" ".join(segment.split()))
    return "; ".join(kept).strip().strip(";").strip()


def clean_examples(texts: Iterable[str]) -> list[str]:
    """Return a synset's examples with the same leakage removed, empties dropped.

    The broken synsets of :func:`clean_definition` leave the matching debris on this side
    too — ``toed.a.01`` has the example ``"long-toed; "`` — so each example is stripped of
    stray quotes and dangling separators, and anything that is then empty is dropped.

    Args:
        texts: ``Synset.examples()``.

    Returns:
        The usable examples, in source order, de-duplicated.
    """
    cleaned: list[str] = []
    for text in texts:
        collapsed = " ".join(text.split()).strip().strip(";").strip().strip('"').strip()
        if collapsed and collapsed not in cleaned:
            cleaned.append(collapsed)
    return cleaned


def lexname_domain(lexname: str) -> DomainTag | None:
    """Map a WordNet lexname onto a taxonomy leaf, or ``None`` for ``tag_domain`` to answer.

    Args:
        lexname: ``Synset.lexname()``, e.g. ``"noun.animal"``.

    Returns:
        The mapped :class:`~opengloss_generator.taxonomy.DomainTag`, or ``None`` when
        :data:`LEXNAME_DOMAIN_MAP` has no unambiguous, specific leaf for it.
    """
    return LEXNAME_DOMAIN_MAP.get(lexname.strip().lower())


# --------------------------------------------------------------------------------------
# Kind
# --------------------------------------------------------------------------------------


def wordnet_kind(
    headword: str, parts: Sequence[PartOfSpeech], *, is_instance: bool = False
) -> LexemeKind:
    """Classify a WordNet headword's :class:`LexemeKind` from WordNet's own evidence.

    The rules, in order:

    1. a lowercase member of :data:`~opengloss_generator.migrate.FUNCTION_WORDS` is a
       function word, whatever WordNet lists it under ("a" is a noun in WordNet — the
       angstrom — and a determiner in English);
    2. a headword WordNet capitalises, or one whose synset has an *instance* hypernym, is
       a proper noun. WordNet lower-cases every common lemma, so a capital here is a
       statement about the word rather than about a sentence position (contrast D-26,
       which has to infer this from prose);
    3. a spaced headword that WordNet lists as a verb and not as a noun is a phrasal verb;
    4. anything else with a space or an internal hyphen is a compound;
    5. anything else is a simplex.

    Abbreviations and affixes are deliberately not decided here: WordNet gives no signal
    for either that the ``classify_kind`` retrofit does not have, and this import writes no
    ``classify_kind`` provenance marker, so that pass revisits every entry it writes.

    Args:
        headword: The surface form, already in WordNet's own casing.
        parts: The parts of speech the lemma has entries under.
        is_instance: Whether any of the lemma's synsets has an instance hypernym — the
            WordNet encoding of "this is a named individual, not a class".

    Returns:
        The kind to store.
    """
    word = headword.strip()
    if word.lower() in FUNCTION_WORDS and not word[:1].isupper():
        return LexemeKind.FUNCTION_WORD
    if is_instance or word[:1].isupper():
        return LexemeKind.PROPER_NOUN
    spaced = any(character.isspace() for character in word)
    if spaced and PartOfSpeech.VERB in parts and PartOfSpeech.NOUN not in parts:
        return LexemeKind.PHRASAL_VERB
    if spaced or "-" in word:
        return LexemeKind.COMPOUND
    return LexemeKind.SIMPLEX


# --------------------------------------------------------------------------------------
# Entry construction
# --------------------------------------------------------------------------------------


def _term(lemma_name: str) -> str:
    """Return a relation target term from a WordNet lemma name: underscores become spaces."""
    return lemma_name.replace("_", " ")


def _matching_lemmas(synset: Any, key: str) -> list[Any]:  # noqa: ANN401 - untyped reader
    """Return the synset's lemmas whose name is ``key``, ignoring case.

    ``synsets(word, pos)`` runs its argument through WordNet's morphological stemmer, so
    looking up "axes" returns *ax* and *axis* synsets. Only synsets that actually list the
    requested form are this headword's, and the lemmas that match are the ones whose
    lemma-level pointers (antonyms, derivations) belong to it.
    """
    lowered = key.lower()
    return [lemma for lemma in synset.lemmas() if lemma.name().lower() == lowered]


def _synsets_for(corpus: Any, key: str, letter: str) -> list[Any]:  # noqa: ANN401 - untyped
    """Return the synsets of ``key`` under one WordNet part of speech, in sense order."""
    return [synset for synset in corpus.synsets(key, pos=letter) if _matching_lemmas(synset, key)]


def _pointed_at(source: Any, pointer: str) -> list[Any]:  # noqa: ANN401 - untyped reader
    """Return one WordNet pointer's targets in a stable order.

    NLTK holds a synset's and a lemma's pointers in a ``set`` and returns them in that
    set's iteration order, which depends on ``PYTHONHASHSEED`` — two imports of the same
    word in two processes produce the same relations in *different orders*. Relation order
    is stored, exported and compared, so it is sorted here by WordNet's own stable name for
    the target (``pooch.n.01``, ``good.a.01.good``), which is a real WordNet ordering
    rather than an alphabetisation of surface forms.

    Args:
        source: A ``Synset`` or a ``Lemma``.
        pointer: The name of the reader method to call, e.g. ``"hyponyms"``.

    Returns:
        The pointer's targets, sorted by their WordNet name.
    """
    return sorted(getattr(source, pointer)(), key=lambda target: str(target.name()))


def _relations_for(synset: Any, lemmas: Sequence[Any], lexeme_id: str) -> list[Relation]:  # noqa: ANN401
    """Return one sense's relations, in a fixed type order, with self-targets dropped.

    Targets are surface forms with no ``sense_id``: the ``resolve`` stage fills those in
    once the target lexeme exists in the store, exactly as it does for a migrated entry.

    Args:
        synset: The WordNet synset the sense was built from.
        lemmas: The synset's lemmas for *this* headword, whose lemma-level antonyms are
            the sense's antonyms (WordNet records antonymy between word forms, not between
            concepts: "good"/"bad" are antonyms, "good"/"evilness" are not).
        lexeme_id: The owning entry's id, so an edge from a lexeme to itself — WordNet's
           ``also_sees`` and ``similar_tos`` occasionally point back into the same
           synset's lemma set — is dropped rather than stored as a self-loop.

    Returns:
        The relations, de-duplicated on ``(type, term)``.
    """
    lemma_level: dict[RelationType, list[str]] = {
        # The synset's own lemmas are synonyms by construction, and come first; the
        # headword's own form among them is dropped by the self-target filter below.
        RelationType.SYNONYM: [_term(name) for name in synset.lemma_names()],
        RelationType.ANTONYM: [
            _term(antonym.name()) for lemma in lemmas for antonym in _pointed_at(lemma, "antonyms")
        ],
    }
    buckets = [
        (
            relation_type,
            lemma_level.get(relation_type, [])
            + [
                _term(_first_lemma_name(target))
                for pointer in pointers
                for target in _pointed_at(synset, pointer)
            ],
        )
        for relation_type, pointers in SYNSET_RELATIONS
    ]
    return [
        relation
        for relation in relations_from_lists(buckets)
        if relation.target.lexeme_id != lexeme_id
    ]


def _first_lemma_name(synset: Any) -> str:  # noqa: ANN401 - untyped reader
    """Return a synset's head lemma name, which is the term an edge points at."""
    return synset.lemma_names()[0]


def _derivations(lemmas: Iterable[Any], headword: str) -> list[str]:
    """Return WordNet's derivationally related forms for a part of speech, de-duplicated.

    This is the *only* morphology the import writes. Plurals, tenses and comparatives are
    left unset for the existing morphology and ``hygiene`` passes: WordNet's exception
    lists cover irregular forms only, so deriving the rest here would mean guessing.

    Args:
        lemmas: This headword's lemmas under one part of speech.
        headword: The entry's surface form, dropped from the result — WordNet relates
            "dog" the noun to "dog" the verb, which is true and is not a derived form.

    Returns:
        The related forms, in WordNet-name order, de-duplicated.
    """
    forms: list[str] = []
    for lemma in lemmas:
        for related in _pointed_at(lemma, "derivationally_related_forms"):
            form = _term(related.name())
            if form and form.lower() != headword.lower() and form not in forms:
                forms.append(form)
    return forms


def _sense_for(synset: Any, lemmas: Sequence[Any], index: int, lexeme_id: str) -> Sense | None:  # noqa: ANN401
    """Build one v3 sense from one synset, or ``None`` if its definition is unusable."""
    definition = clean_definition(synset.definition())
    if not definition:
        return None
    examples = Renditions[Example](root=[])
    for text in clean_examples(synset.examples()):
        # Span-less by design: the `spans` retrofit finds the headword form for free, and
        # a span guessed here would have to be re-checked by it anyway.
        examples.add(canonical_rendition(Example(text=text)))
    lexname = synset.lexname()
    domain = lexname_domain(lexname)
    return Sense(
        index=index,
        gloss=Renditions[str](root=[canonical_rendition(definition)]),
        examples=examples,
        relations=_relations_for(synset, lemmas, lexeme_id),
        domain=domain,
        domain_hint=None if domain is not None else lexname,
    )


def entry_for(
    word: str,
    *,
    corpus: Any,  # noqa: ANN401 - untyped reader
    entity_type: EntityType | None = None,
    wikidata_qid: str | None = None,
) -> Lexeme | None:
    """Build one schema-v3 entry from every WordNet sense of a headword.

    Args:
        word: A candidate word, spaced or underscored, in any case. WordNet's own casing
            of the lemma wins, so ``"a battery"`` is stored as ``A battery`` and
            ``"11 november"`` as ``11 November``.
        corpus: The reader from :func:`load_wordnet`.
        entity_type: The entity type a candidate list already knows for this name (D-81),
            used verbatim when the entry turns out to be a proper noun. ``None`` falls
            back to WordNet's own evidence — the supersense of the synset's instance
            hypernym, through :func:`entity_type_for_lexnames` — and only then to
            :attr:`~opengloss_generator.schema.EntityType.OTHER`, which is a placeholder
            and is now written *last* rather than always (D-12, D-18).
        wikidata_qid: The QID the same candidate list carries. Stored as the join key
            every later name pass and every entity-typing audit needs; it costs nothing
            and cannot be re-derived from the store.

    Returns:
        The validated entry, or ``None`` when WordNet has no synset listing this exact
        form (the morphological stemmer's near-misses do not count).
    """
    key = normalise(word)
    if not key:
        return None

    by_pos: list[tuple[PartOfSpeech, list[Any]]] = []
    headword: str | None = None
    is_instance = False
    instance_lexnames: list[str] = []
    for letter, pos in POS_LETTERS:
        synsets = _synsets_for(corpus, key, letter)
        if not synsets:
            continue
        by_pos.append((pos, synsets))
        for synset in synsets:
            if headword is None:
                headword = _term(_matching_lemmas(synset, key)[0].name())
            instances = synset.instance_hypernyms()
            is_instance = is_instance or bool(instances)
            instance_lexnames.extend(hypernym.lexname() for hypernym in instances)
    if headword is None:
        return None

    lexeme_id = slugify(headword)
    kind = wordnet_kind(headword, [pos for pos, _ in by_pos], is_instance=is_instance)
    typed = entity_type or entity_type_for_lexnames(instance_lexnames) or EntityType.OTHER
    entry = Lexeme(
        lexeme_id=lexeme_id,
        headword=headword,
        language="en",
        kind=kind,
        proper_noun=(
            ProperNounInfo(entity_type=typed, wikidata_qid=wikidata_qid)
            if kind is LexemeKind.PROPER_NOUN
            else None
        ),
        # Partial, and honestly so: WordNet supplies glosses, examples and a graph, and
        # none of etymology, encyclopedia text, the usage note or any non-canonical
        # rendition. The enrichment chain is what makes one of these complete.
        status=EntryStatus.PARTIAL,
    )
    for pos, synsets in by_pos:
        senses: list[Sense] = []
        lemmas: list[Any] = []
        for synset in synsets:
            matched = _matching_lemmas(synset, key)
            sense = _sense_for(synset, matched, len(senses), lexeme_id)
            if sense is None:
                continue
            lemmas.extend(matched)
            senses.append(sense)
        if not senses:
            continue
        entry.pos_entries.append(
            POSEntry(
                pos=pos,
                senses=senses,
                morphology=Morphology(derivations=_derivations(lemmas, headword)),
            )
        )
    if not entry.pos_entries:
        return None

    _stamp_provenance(entry)
    return Lexeme.model_validate(entry.model_dump(mode="json"))


def iter_entries(
    words: Iterable[str],
    *,
    corpus: Any,  # noqa: ANN401
    candidates: Mapping[str, CandidateRow] | None = None,
) -> Iterator[tuple[str, Lexeme | None]]:
    """Yield ``(word, entry)`` for every candidate, ``None`` where WordNet has no entry.

    Args:
        words: Candidate words, in the order they should be imported.
        corpus: The reader from :func:`load_wordnet`.
        candidates: Optional ``lexeme_id -> row`` index from :func:`candidate_index`,
            supplying each name's entity type and QID (D-81). A word the index does not
            name is imported exactly as before.

    Yields:
        The word as given and the entry built from it, so a caller can count and name the
        misses without looking them up a second time.
    """
    for word in words:
        row = (candidates or {}).get(slugify(word))
        yield (
            word,
            entry_for(
                word,
                corpus=corpus,
                entity_type=row.entity_type if row else None,
                wikidata_qid=row.wikidata_qid if row else None,
            ),
        )


# --------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------


def _record(field: str) -> Provenance:
    """Return the zero-cost provenance record accounting for one inherited field."""
    return Provenance(
        stage=StageName.MIGRATE,
        model=WORDNET_MODEL,
        prompt_version=WORDNET_PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
        note=f"{field}: {WORDNET_NOTE}",
    )


def _stamp_provenance(entry: Lexeme) -> None:
    """Add one provenance record per inherited field and point the content at it.

    Records are added in :data:`PROVENANCE_FIELDS` order, and only for a field the entry
    actually carries, so the ids are stable for a given entry and no record claims content
    WordNet did not supply. Glosses, examples and relations carry a ``provenance_id`` back
    to their record; ``domain`` and ``morphology`` have no such slot on the schema, so
    their records stand alone and are found by their note.

    Args:
        entry: The entry to stamp, mutated in place.
    """
    senses = [sense for _, sense, _ in entry.iter_senses()]
    present = {
        "gloss": bool(senses),
        "examples": any(len(sense.examples) for sense in senses),
        "relations": any(sense.relations for sense in senses),
        "morphology": any(pos.morphology.derivations for pos in entry.pos_entries),
        "domain": any(sense.domain is not None or sense.domain_hint for sense in senses),
    }
    ids = {
        field: entry.add_provenance(_record(field)) for field in PROVENANCE_FIELDS if present[field]
    }
    for sense in senses:
        _point_at(sense.gloss, ids.get("gloss"))
        _point_at(sense.examples, ids.get("examples"))
        for relation in sense.relations:
            relation.provenance_id = ids.get("relations")


def _point_at[T](renditions: Renditions[T], provenance_id: str | None) -> None:
    """Set ``provenance_id`` on every rendition in a set."""
    rendition: Rendition[T]
    for rendition in renditions:
        rendition.provenance_id = provenance_id
