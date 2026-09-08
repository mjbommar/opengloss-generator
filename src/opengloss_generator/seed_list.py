"""Read a named-entity candidate TSV into seeds for ``generate`` (D-82).

``data/core/tier6_candidates.tsv`` is the tier-6 builder's output: one row per candidate
name, with the entity type and the Wikidata id that produced it already decided. Rows
whose ``source`` is ``wordnet`` are imported for free by ``import-wordnet``; the rest —
``name_seed`` — have no free source and are what this module feeds to the seeded
``generate`` path.

The reader is deliberately the shape of
:func:`opengloss_generator.wordnet_import.read_candidates`: header-driven, ``source``
filtered, order-preserving, duplicate-dropping. It differs only in reading five columns
instead of one, and in keeping the ``name`` column rather than ``word`` — ``name`` is the
display form (*John F. Kennedy*), and case cannot be recovered from the slug.

Two derived fields are computed here rather than read, because the TSV does not carry
them:

* **the hypernym** — what class of thing the entity is. The plan (NAMED-ENTITY-PLAN § 4e)
  wants WordNet's shape of gloss, *Denver is a **city***, which needs a class noun. A
  Wikidata ``P31`` class would be the right source and the builder did not keep one, so
  this derives a **type-appropriate default** — ``person`` → "person", ``work`` → "work"
  — refined only where the ``us_list`` token in ``notes`` names something more specific
  (``us_city_100k`` → "city", ``us_national_park`` → "national park"). That covers about
  800 of the 11,120 rows; the other 10,000-odd get the bare type noun, which is honest
  rather than precise, and the model is free to be more specific in the gloss itself.
* **the domain hint** — free text under D-17, never the binding tag, which comes from the
  enum-constrained ``DraftSense.domain``. Same derivation, same refinement.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opengloss_generator.schema import EntityType
from opengloss_generator.taxonomy import DomainTag

__all__ = [
    "NAME_SEED_SOURCE",
    "SeedRow",
    "domain_hint_for",
    "hypernym_for",
    "read_seed_list",
]

#: The ``source`` cell of a tier-6 row this path owns; the other value is ``wordnet``,
#: which ``import-wordnet`` takes for free.
NAME_SEED_SOURCE = "name_seed"

#: Required columns. ``name`` carries display case, ``word`` does not always differ but
#: is not read; ``qid`` is present on every row of the current list.
_REQUIRED = ("name", "entity_type")

#: Fallback class noun per entity type, used when nothing more specific is known. Chosen
#: to be true rather than informative: "Alexander Hamilton is a person" tells the model
#: only what the type already said, which is the point — it is better than a guessed
#: "statesman" that the sources never asserted.
_HYPERNYM_BY_TYPE: dict[EntityType, str] = {
    EntityType.PERSON: "person",
    EntityType.PLACE: "place",
    EntityType.ORGANIZATION: "organization",
    EntityType.WORK: "work",
    EntityType.EVENT: "event",
    EntityType.PRODUCT: "product",
    EntityType.SPECIES: "species",
    EntityType.OTHER: "named entity",
}

#: The ``us_list`` tokens the builder writes into ``notes``, and the class noun each one
#: establishes. A row carrying several takes the first match in this order, so
#: ``us_president,us_vice_president`` reads as the higher office.
_HYPERNYM_BY_US_LIST: tuple[tuple[str, str], ...] = (
    ("us_president", "president of the United States"),
    ("us_vice_president", "vice president of the United States"),
    ("scotus_justice", "justice of the Supreme Court of the United States"),
    ("us_cabinet_sec", "United States cabinet secretary"),
    ("us_fed_department", "federal executive department of the United States"),
    ("us_fed_agency", "federal agency of the United States"),
    ("us_amendment", "amendment to the United States Constitution"),
    ("us_state_capital", "city"),
    ("us_city_100k", "city"),
    ("us_state", "state of the United States"),
    ("world_country", "country"),
    ("us_national_park", "national park"),
    ("us_natl_monument", "national monument"),
    ("us_university", "university"),
    ("sp500_company", "company"),
    ("dow30_company", "company"),
    ("nfl_team", "professional American football team"),
    ("nba_team", "professional basketball team"),
    ("mlb_team", "professional baseball team"),
    ("nhl_team", "professional ice hockey team"),
    ("mls_team", "professional soccer team"),
    ("us_battle", "battle"),
    ("us_conflict", "war"),
    ("nobel_peace", "Nobel Peace Prize laureate"),
    ("nobel_laureate", "Nobel laureate"),
)

#: Domain leaves the taxonomy does not have yet. NAMED-ENTITY-PLAN § 4d proposes
#: ``nature.settlements`` and ``law_government.polities``; until they land, each falls
#: back to the nearest leaf that does exist. The hint is free text either way (D-17), so
#: naming a leaf the enum lacks is not an error — but a hint the taxonomy will recognise
#: steers the ``senses`` call better, so the fallback is used rather than the wish.
_PROPOSED_LEAVES: dict[str, DomainTag] = {
    "nature.settlements": DomainTag.PEOPLE_SOCIETY_COMMUNITY_LIFE,
    "law_government.polities": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE,
}

_DOMAIN_BY_TYPE: dict[EntityType, str] = {
    EntityType.PERSON: DomainTag.HISTORY_HISTORICAL_FIGURES.value,
    EntityType.PLACE: DomainTag.NATURE_LANDFORMS.value,
    EntityType.ORGANIZATION: DomainTag.BUSINESS_GENERAL.value,
    EntityType.WORK: DomainTag.ARTS_GENERAL.value,
    EntityType.EVENT: DomainTag.HISTORY_GENERAL.value,
    EntityType.PRODUCT: DomainTag.BUSINESS_GENERAL.value,
    EntityType.SPECIES: DomainTag.NATURE_ANIMALS.value,
    EntityType.OTHER: DomainTag.HUMANITIES_GENERAL.value,
}

_DOMAIN_BY_US_LIST: tuple[tuple[str, str], ...] = (
    ("us_president", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value),
    ("us_vice_president", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value),
    ("scotus_justice", DomainTag.LAW_GOVERNMENT_COURTS_JUSTICE.value),
    ("us_cabinet_sec", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value),
    ("us_fed_department", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value),
    ("us_fed_agency", DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value),
    ("us_amendment", DomainTag.LAW_GOVERNMENT_CONSTITUTIONAL_LAW.value),
    ("us_state_capital", "nature.settlements"),
    ("us_city_100k", "nature.settlements"),
    ("us_state", "law_government.polities"),
    ("world_country", "law_government.polities"),
    ("us_national_park", DomainTag.NATURE_CONSERVATION.value),
    ("us_natl_monument", DomainTag.NATURE_CONSERVATION.value),
    ("us_university", DomainTag.EDUCATION_HIGHER_EDUCATION.value),
    ("sp500_company", DomainTag.BUSINESS_GENERAL.value),
    ("dow30_company", DomainTag.BUSINESS_GENERAL.value),
    ("nfl_team", DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value),
    ("nba_team", DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value),
    ("mlb_team", DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value),
    ("nhl_team", DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value),
    ("mls_team", DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value),
    ("us_battle", DomainTag.HISTORY_GENERAL.value),
    ("us_conflict", DomainTag.HISTORY_GENERAL.value),
)


@dataclass(frozen=True, slots=True)
class SeedRow:
    """One candidate name and everything a source already knows about it."""

    name: str
    entity_type: EntityType
    wikidata_qid: str | None
    hypernym: str
    domain_hint: str


def _us_list_tokens(notes: str) -> set[str]:
    """Return the ``us_list=`` tokens a tier-6 ``notes`` cell carries.

    The cell is a ``; ``-joined list of free-text observations, one of which may be
    ``us_list=a,b,c``. Anything else in it is ignored.
    """
    for part in notes.split(";"):
        stripped = part.strip()
        if stripped.startswith("us_list="):
            return {token.strip() for token in stripped.removeprefix("us_list=").split(",")}
    return set()


def hypernym_for(entity_type: EntityType, notes: str = "") -> str:
    """Return the class noun to tell the senses call this entity belongs to.

    Args:
        entity_type: The row's entity type.
        notes: The row's ``notes`` cell, read only for its ``us_list`` tokens.

    Returns:
        A bare noun phrase, never empty.
    """
    tokens = _us_list_tokens(notes)
    for token, hypernym in _HYPERNYM_BY_US_LIST:
        if token in tokens:
            return hypernym
    return _HYPERNYM_BY_TYPE[entity_type]


def domain_hint_for(entity_type: EntityType, notes: str = "") -> str:
    """Return the free-text domain hint for a row, resolving proposed leaves.

    Args:
        entity_type: The row's entity type.
        notes: The row's ``notes`` cell, read only for its ``us_list`` tokens.

    Returns:
        A taxonomy leaf value. A leaf NAMED-ENTITY-PLAN § 4d proposes but the enum does
        not yet define falls back to the nearest one that exists.
    """
    tokens = _us_list_tokens(notes)
    hint = _DOMAIN_BY_TYPE[entity_type]
    for token, candidate in _DOMAIN_BY_US_LIST:
        if token in tokens:
            hint = candidate
            break
    if hint in _PROPOSED_LEAVES:
        try:
            return DomainTag(hint).value
        except ValueError:
            return _PROPOSED_LEAVES[hint].value
    return hint


def read_seed_list(path: Path, *, source: str | None = NAME_SEED_SOURCE) -> list[SeedRow]:
    """Read a tier-6 candidate TSV into seeds, in file order.

    Args:
        path: The TSV to read. Its header row must name ``name`` and ``entity_type``;
            ``qid`` and ``notes`` are read when present and are not required. A
            ``source`` column is required only when ``source`` is given.
        source: Keep only rows whose ``source`` cell equals this. ``None`` keeps every
            row, for a hand-written list with no ``source`` column.

    Returns:
        One :class:`SeedRow` per usable row, duplicates on ``name`` dropped, in file
        order. A row whose ``entity_type`` cell is empty or is not an
        :class:`~opengloss_generator.schema.EntityType` member is skipped rather than
        defaulted: an unseeded name belongs on the ordinary ``generate`` path, not on
        this one with a wrong type asserted about it.

    Raises:
        ValueError: If the file has no header row, is missing a required column, or has
            no ``source`` column when one was asked for.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().lower() for cell in lines[0].split("\t")]
    for column in _REQUIRED:
        if column not in header:
            raise ValueError(f"{path} has no {column!r} column in its header row")
    name_at = header.index("name")
    type_at = header.index("entity_type")
    qid_at = header.index("qid") if "qid" in header else None
    notes_at = header.index("notes") if "notes" in header else None
    source_at: int | None = None
    if source is not None:
        if "source" not in header:
            raise ValueError(f"{path} has no 'source' column, so --source {source!r} cannot apply")
        source_at = header.index("source")

    rows: list[SeedRow] = []
    seen: set[str] = set()

    def cell(cells: list[str], index: int | None) -> str:
        return cells[index].strip() if index is not None and index < len(cells) else ""

    for line in lines[1:]:
        cells = line.split("\t")
        if name_at >= len(cells) or type_at >= len(cells):
            continue
        if source_at is not None and cell(cells, source_at) != source:
            continue
        name = cells[name_at].strip()
        if not name or name in seen:
            continue
        try:
            entity_type = EntityType(cell(cells, type_at))
        except ValueError:
            continue
        seen.add(name)
        notes = cell(cells, notes_at)
        qid = cell(cells, qid_at)
        rows.append(
            SeedRow(
                name=name,
                entity_type=entity_type,
                wikidata_qid=qid or None,
                hypernym=hypernym_for(entity_type, notes),
                domain_hint=domain_hint_for(entity_type, notes),
            )
        )
    return rows
