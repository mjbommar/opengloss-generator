"""Clean `data/core/tier6_candidates.tsv` into `data/core/tier6_seeds.tsv` (D-83).

D-82's 100-row pilot of the seeded ``generate`` path found three problems upstream of
the model: 8 of 100 rows were mis-typed in the TSV (7 produced a damaged gloss —
*"Arianism is a named place..."*, *"NGC 6302 is a named astronomical event..."*, *"Mil
Mi-24 is a work..."*); 604 of 11,120 rows carry a Wikipedia disambiguator ("Bonnie and
Clyde (film)") that leaks into examples; and the crude per-type domain hint in
``seed_list.py`` mis-tagged organisations that are not businesses (the United Nations,
the Church of England) because ``organization`` has no safe single-root default. This
script fixes all three, free where a free source answers the question and one batched
``gpt-5.4-nano`` call per 30-50 names otherwise, and writes a TSV `seed_list.read_seed_list`
consumes with no code change.

What it does, in order:

1. **Disambiguator stripping.** ``"Bonnie and Clyde (film)"`` -> headword
   ``"Bonnie and Clyde"``, ``disambiguator="film"``, via the trailing
   ``" (...)"`` a Wikipedia title carries (the same shape
   ``build_tier6_candidates.PARENTHETICAL`` already strips at build time — most rows
   already carry the bare form in their own ``notes`` cell as
   ``strip_parenthetical='...'``; this script recomputes it independently rather than
   trusting that text, so it holds even for a hand-edited TSV that never carries it).
   A stripped headword that collides — with a live entry in the store, or with another
   row's own stripped headword — is kept, never dropped: a note is appended
   (``headword_collision: store has '<slug>'`` / ``headword_collision: also produced by
   '<name>' (row N)``) for `lexeme-hygiene --only aliases` (D-81 decision 4) to resolve
   once both sides exist.
2. **Retyping.** Every ``name_seed`` row is sent to ``gpt-5.4-nano`` (reasoning effort
   ``none``, matching ``StageName.CLASSIFY_KIND``'s policy — this is a strict-enum
   verdict, not prose, so the HYGIENE policy's low-effort prose budget is not needed;
   D-47 idempotence markers are not needed either, since this is a one-shot pass over a
   TSV rather than a store retrofit) 40 names per call, strict enum over
   :class:`~opengloss_generator.schema.EntityType` plus ``not_a_named_entity``, given
   the name, the TSV's type, the Wikidata P31 class labels from
   ``tier6_cache/class_labels.json`` (fetched here, cached, keyed off
   ``wd_facts.json``'s own ``p31`` lists — the cache the tier-6 builder left behind has
   no *label* for a P31 class, only the broad bucket it resolves to) and the vital
   article's topic/section. The verdict overrides the TSV; ``not_a_named_entity``
   drops the row (``dropped_reason="not_a_named_entity"``) by writing a sentinel into
   its ``entity_type``/``entity_type_final`` cells that is not a member of
   :class:`~opengloss_generator.schema.EntityType`, so ``read_seed_list``'s existing
   ``try: EntityType(...) except ValueError: continue`` already skips it — no reader
   change needed.

   ``type_source`` records *why* ``entity_type_final`` is what it is, not merely
   whether it changed: ``verdict`` when the model disagreed with the TSV and the
   verdict was taken; ``tsv`` when the model agreed and neither this script's own
   deterministic Wikidata recomputation (``class_types.json``'s already-resolved broad
   category for the row's own ``p31`` classes — the same signal
   ``entity_type_of`` uses at build time, recomputed here for free) had an independent
   opinion; ``wikidata`` when the model agreed *and* that free recomputation
   independently agreed too, which is the one case this script can say the type rests
   on more than the TSV's own say-so.
3. **Hypernym and domain hint.** The hypernym is the clearest available class noun: a
   recognised Wikidata P31 label when the cache has one (``"city"``, ``"sovereign
   state"``, ``"painting"``, ...), else ``seed_list.hypernym_for``'s existing
   type/``us_list`` derivation. The domain hint is looked up from that same label
   through :data:`_LABEL_DOMAIN` — a small table, not the per-type default
   ``seed_list.py`` uses, because D-82 showed the per-type default is actively wrong
   for ``organization`` (no single root fits a company, a church and a federal agency)
   and, now that D-81 added ``nature.settlements``/``law_government.polities``, for
   ``place`` too (most tier-6 places are one of those two, not physical geography).
   Both are left blank rather than defaulted when no label matches, so
   ``tag_domain`` decides with no bad prior to fight. ``person`` keeps a default
   (``history.historical_figures``) refined by occupation (``P106``, fetched here for
   ``person``-type rows only and cached the same way as the class labels) toward
   ``science.general``, ``arts.visual_art``, ``humanities.literature``, ``arts.music``,
   ``arts.film``, ``sports_recreation.general``, ``law_government.government_structure``
   or ``humanities.religion`` when the occupation says so; ``event`` keeps
   ``history.general``, refined toward ``history.world_wars``/``history.modern_history``
   by a battle/war label plus a year found in the headword; ``work``, ``other``,
   ``product`` and ``species`` keep the old per-type default (``arts.general``,
   ``humanities.general``, ``business.general``, ``nature.animals``) since nothing in
   the pilot or in this run's disagreement table showed those wrong the way
   ``organization``'s was.
4. **Living-person and recency flags.** Carried through, not filtered on: a token
   (``living_person`` / ``recent_creation``) is appended to ``notes`` from the same
   ``wd_facts.json`` dates and thresholds ``build_tier6_candidates.py`` already scores
   by (``LIVING_BIRTH_YEAR=1935`` with no recorded death; ``RECENT_INCEPTION_YEAR=2015``
   inception), so a later pass can act on it without re-deriving it. Nothing here drops
   a row for being recent or living — NAMED-ENTITY-PLAN's own scoring penalises that,
   it does not exclude it, and this script is not the place to second-guess that.

``source == wordnet`` rows pass through untouched beyond the headword/disambiguator
split above: ``import-wordnet`` already gives them a real, free hypernym (WordNet's own
instance hypernym), so nothing here should paper over that with a guess, and
`entity_type_final`/`type_source`/`hypernym`/`domain_hint` are left as `tsv`/blank for
them (``dropped_reason`` is always blank — this script never drops a WordNet row).

The output keeps every column `tier6_candidates.tsv` has, in the same order, plus
``headword``, ``disambiguator``, ``entity_type_final``, ``type_source``,
``domain_hint``, ``hypernym`` and ``dropped_reason`` appended at the end — with one
deliberate exception: the existing ``name`` and ``entity_type`` cells are *overwritten*
in place (``name`` becomes the headword, ``entity_type`` becomes
``entity_type_final``, or the ``not_a_named_entity`` drop sentinel), so
``seed_list.read_seed_list`` needs no change to consume this file — it already reads
exactly those two columns, filtered by ``source``. ``headword`` and
``entity_type_final`` stay present anyway as explicit, self-describing columns; nobody
auditing this file should have to know that convention to find the value driving the
`generate` path.

Usage::

    uv run python scripts/clean_tier6_seeds.py \
        --candidates data/core/tier6_candidates.tsv \
        --cache data/core/tier6_cache \
        --store data/core-store \
        --out data/core/tier6_seeds.tsv \
        --budget 2.00

    # skip every network fetch (Wikidata labels, occupations) and reuse whatever the
    # cache already has -- for a repeat run or a test
    uv run python scripts/clean_tier6_seeds.py --offline ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import infer_model
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from opengloss_generator.config import StoreConfig
from opengloss_generator.identity import slugify
from opengloss_generator.pricing import ServiceTier, estimate_cost
from opengloss_generator.schema import EntityType
from opengloss_generator.seed_list import NAME_SEED_SOURCE, domain_hint_for, hypernym_for
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag

__all__ = [
    "CandidateRow",
    "RetypeVerdict",
    "build_output_header",
    "candidate_wikidata_type",
    "domain_hint_from_label",
    "hypernym_from_label",
    "living_and_recent_notes",
    "occupation_domain",
    "read_tsv",
    "strip_disambiguator",
    "write_tsv",
]

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

USER_AGENT = "OpenGloss-tier6-seeds/0.1 (https://github.com/mjbommar; michael@bommaritollc.com)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"

RETYPE_MODEL = "gpt-5.4-nano"
RETYPE_BATCH_SIZE = 40
RETYPE_MAX_TOKENS = 4096
RETYPE_PROMPT_CACHE_KEY = "opengloss:tier6_retype"

#: Trailing Wikipedia-style disambiguator, e.g. ``"Bonnie and Clyde (film)"``. Same shape
#: as ``build_tier6_candidates.PARENTHETICAL``, recomputed independently here rather than
#: trusted from that script's ``notes`` cell (see module docstring, step 1).
_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")

_YEAR = re.compile(r"(1[4-9]\d{2}|20\d{2})")
_WORLD_WAR_YEARS = range(1914, 1946)

LIVING_BIRTH_YEAR = 1935
RECENT_INCEPTION_YEAR = 2015

REQUIRED_COLUMNS = ("name", "word", "entity_type", "source", "qid", "notes")
NEW_COLUMNS = (
    "headword",
    "disambiguator",
    "entity_type_final",
    "type_source",
    "domain_hint",
    "hypernym",
    "dropped_reason",
)
NOT_A_NAMED_ENTITY = "not_a_named_entity"


# --------------------------------------------------------------------------------------
# TSV I/O -- deliberately plain-text, matching seed_list.read_seed_list's own convention
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class CandidateRow:
    """One `tier6_candidates.tsv` row, as a header-keyed dict plus its file position."""

    index: int
    cells: dict[str, str]


def read_tsv(path: Path) -> tuple[list[str], list[CandidateRow]]:
    """Read a tab-separated file into a header and header-keyed rows.

    Args:
        path: The TSV to read. Its first non-blank line is the header.

    Returns:
        ``(header, rows)`` in file order.

    Raises:
        ValueError: If the file is empty or is missing a column this script requires.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path} is empty")
    header = [cell.strip() for cell in lines[0].split("\t")]
    for column in REQUIRED_COLUMNS:
        if column not in header:
            raise ValueError(f"{path} has no {column!r} column in its header row")
    rows: list[CandidateRow] = []
    for index, line in enumerate(lines[1:]):
        cells = line.split("\t")
        row = {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(header)}
        rows.append(CandidateRow(index=index, cells=row))
    return header, rows


def build_output_header(header: list[str]) -> list[str]:
    """Return the output column order: the input header, then the new columns."""
    return [*header, *(c for c in NEW_COLUMNS if c not in header)]


def write_tsv(path: Path, header: list[str], rows: list[CandidateRow]) -> None:
    """Write header-keyed rows back out as a TSV, in ``header`` order."""
    lines = ["\t".join(header)]
    for row in rows:
        lines.append("\t".join(row.cells.get(column, "") for column in header))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# Step 1 -- disambiguator stripping and collision notes
# --------------------------------------------------------------------------------------


def strip_disambiguator(name: str) -> tuple[str, str]:
    """Split a Wikipedia-style title into ``(headword, disambiguator)``.

    Args:
        name: The candidate's display name, e.g. ``"Bonnie and Clyde (film)"``.

    Returns:
        ``(headword, disambiguator)``. ``disambiguator`` is ``""`` when ``name`` carries
        no trailing parenthetical.
    """
    match = _TRAILING_PAREN.search(name)
    if not match:
        return name, ""
    headword = name[: match.start()].strip()
    disambiguator = match.group(1).strip()
    if not headword:
        # A name that is *only* a parenthetical ("(untitled)") is not a real split.
        return name, ""
    return headword, disambiguator


def living_and_recent_notes(facts: dict[str, Any] | None) -> list[str]:
    """Return ``living_person``/``recent_creation`` tokens for a row's Wikidata facts.

    Mirrors ``build_tier6_candidates.py``'s own thresholds (``LIVING_BIRTH_YEAR``,
    ``RECENT_INCEPTION_YEAR``) so the two scripts never disagree about what "recent"
    means. This only *records* the flag in ``notes``; nothing here drops a row for it
    (see module docstring, step 4).
    """
    if not facts:
        return []
    notes: list[str] = []
    dob_year = _year_of(facts.get("dob", ""))
    dod_year = _year_of(facts.get("dod", ""))
    if dob_year is not None and dob_year >= LIVING_BIRTH_YEAR and dod_year is None:
        notes.append("living_person")
    inception_year = _year_of(facts.get("inception", ""))
    if inception_year is not None and inception_year >= RECENT_INCEPTION_YEAR:
        notes.append("recent_creation")
    return notes


def _year_of(date: str) -> int | None:
    """Return the leading year of an ISO-ish date string, or ``None``."""
    match = re.match(r"^(-?\d{4})", date or "")
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------------------
# Cache loading -- the tier-6 builder's own raw cache, read only
# --------------------------------------------------------------------------------------


def load_cache_json(cache_dir: Path, name: str) -> dict[str, Any]:
    """Load one of the tier-6 builder's cache files.

    Args:
        cache_dir: ``data/core/tier6_cache``.
        name: The file name, e.g. ``"wd_facts.json"``.

    Returns:
        The parsed JSON, or ``{}`` if the file is absent (offline runs against a
        partial cache should degrade, not crash).
    """
    path = cache_dir / name
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# Wikidata fetchers -- MediaWiki's `wbgetentities` for labels (stable), WDQS for the one
# thing it alone can answer, `P106` occupation (observed live 2026-09-08 to be under an
# active rate-limiting incident at query.wikidata.org: retried with backoff, and a
# persistent failure degrades to "no occupation data" rather than aborting the run --
# see `fetch_occupations`).
# --------------------------------------------------------------------------------------


def _http_get(url: str, *, accept: str | None = None, timeout: int = 30) -> bytes:
    """GET one URL with this script's user agent, retrying transient failures."""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    last: Exception | None = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers=headers)  # noqa: S310
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read()
        except Exception as error:
            last = error
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed after 5 attempts: {url}: {last}")


def fetch_labels(qids: list[str], *, batch_size: int = 50) -> dict[str, str]:
    """Return ``QID -> English label`` for a list of Wikidata item ids.

    Uses `wbgetentities`, not WDQS: it is a plain, unauthenticated MediaWiki API call
    with no query-language cost and (observed 2026-09-08) unaffected by the WDQS
    incident `fetch_occupations` has to work around.

    Args:
        qids: Wikidata item ids to label. Duplicates and blanks are ignored.
        batch_size: Ids per request; 50 is `wbgetentities`'s own unauthenticated cap.

    Returns:
        ``{qid: label}``. A QID with no English label, or that could not be resolved,
        is simply absent rather than mapped to ``""``.
    """
    unique = sorted({q for q in qids if q})
    labels: dict[str, str] = {}
    for start in range(0, len(unique), batch_size):
        chunk = unique[start : start + batch_size]
        url = f"{WIKIDATA_API}?" + urllib.parse.urlencode(
            {
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": "labels",
                "languages": "en",
                "format": "json",
            }
        )
        payload = json.loads(_http_get(url))
        for qid, entity in payload.get("entities", {}).items():
            label = entity.get("labels", {}).get("en", {}).get("value")
            if label:
                labels[qid] = label
        time.sleep(0.2)
    return labels


def load_or_fetch_labels(
    cache_dir: Path, file_name: str, qids: set[str], *, offline: bool
) -> dict[str, str]:
    """Return ``QID -> English label`` for one label cache file, fetching what it lacks.

    ``tier6_cache/class_types.json`` (built by ``build_tier6_candidates.py``) already
    resolves every P31 class to the *broad* category (person/place/.../CONCEPT) this
    script's deterministic recomputation uses; it has no human-readable label, which is
    what the hypernym/domain table (step 3) needs, so this script owns two small label
    caches of its own -- ``class_labels.json`` for P31 classes, ``occupation_labels.json``
    for P106 occupations -- both fetched the same way via :func:`fetch_labels`.
    """
    path = cache_dir / file_name
    labels: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    missing = sorted(qids - labels.keys())
    if missing and not offline:
        fresh = fetch_labels(missing)
        labels.update(fresh)
        path.write_text(json.dumps(labels, indent=2, sort_keys=True), encoding="utf-8")
    return labels


def fetch_occupations(qids: list[str], *, batch_size: int = 150) -> dict[str, list[str]]:
    """Return ``person QID -> [occupation (P106) QIDs]`` via WDQS.

    Args:
        qids: The candidates' own Wikidata ids (``person``-typed rows only -- P106 is
            meaningless for anything else).
        batch_size: Kept well under WDQS's own limits; a 429 here still means "slow
            down", not "the item list is too large" (observed live 2026-09-08:
            "Aggressively rate-limiting to 1 req/min - this rule was created during
            active wdqs outage").

    Returns:
        ``{qid: [occupation qids]}``. Empty (rather than raising) if every batch fails
        after retries -- occupation-refined domains are a quality improvement over the
        plain per-type default, not a correctness requirement (see module docstring,
        step 3): a run that cannot reach WDQS still produces a usable file.
    """
    out: dict[str, list[str]] = {}
    unique = sorted({q for q in qids if q})
    for start in range(0, len(unique), batch_size):
        chunk = unique[start : start + batch_size]
        values = " ".join(f"wd:{q}" for q in chunk)
        query = (
            'SELECT ?item (GROUP_CONCAT(DISTINCT ?occx; separator=",") AS ?occ) WHERE { '
            f"VALUES ?item {{ {values} }} OPTIONAL {{ ?item wdt:P106 ?occx }} "
            "} GROUP BY ?item"
        )
        url = f"{WDQS}?" + urllib.parse.urlencode({"query": query})
        try:
            payload = json.loads(_http_get(url, accept="application/sparql-results+json"))
        except RuntimeError as exc:
            sys.stderr.write(
                f"warning: occupation fetch gave up on a batch of {len(chunk)}: {exc}\n"
            )
            continue
        for binding in payload.get("results", {}).get("bindings", []):
            item_qid = binding["item"]["value"].rsplit("/", 1)[-1]
            occ_value = binding.get("occ", {}).get("value", "")
            out[item_qid] = [uri.rsplit("/", 1)[-1] for uri in occ_value.split(",") if uri]
        time.sleep(1.0)
    return out


def load_or_fetch_occupations(
    cache_dir: Path, qids: list[str], *, offline: bool
) -> dict[str, list[str]]:
    """Return ``person QID -> [occupation QIDs]``, cached under ``occupations.json``."""
    path = cache_dir / "occupations.json"
    occupations: dict[str, list[str]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    missing = sorted(set(qids) - occupations.keys())
    if missing and not offline:
        occupations.update(fetch_occupations(missing))
        path.write_text(json.dumps(occupations, indent=2, sort_keys=True), encoding="utf-8")
    return occupations


# --------------------------------------------------------------------------------------
# Step 2 -- the deterministic (free) half of retyping
# --------------------------------------------------------------------------------------

#: Priority order when a row's P31 classes resolve to more than one broad category via
#: `class_types.json` -- identical to `build_tier6_candidates.CLASS_TOPS`'s own
#: `TYPE_PRIORITY`, so this recomputation can never disagree with what the TSV's own
#: `entity_type` column was built from for the reason that order differs.
_TYPE_PRIORITY = ("person", "place", "organization", "work", "event", "species", "other")
_REJECT_MARKERS = frozenset({"DISAMBIG", "LIST", "CATEGORY", "CONCEPT", "UNTYPED"})


def candidate_wikidata_type(
    qid: str, wd_facts: dict[str, Any], class_types: dict[str, list[str]]
) -> EntityType | None:
    """Recompute a candidate's entity type from Wikidata alone, for free.

    Mirrors `build_tier6_candidates.entity_type_of`'s curated-class-then-priority logic,
    using only the cache that script already left behind (`wd_facts.json`'s `p31` list,
    `class_types.json`'s broad-category resolution of each class) -- no network call.
    This is what lets `type_source` distinguish "the model rubber-stamped the TSV" from
    "Wikidata itself, independent of the TSV and the model, says the same thing".

    Args:
        qid: The candidate's own Wikidata id.
        wd_facts: Parsed ``wd_facts.json``.
        class_types: Parsed ``class_types.json``.

    Returns:
        The recomputed type, or ``None`` when the classes are absent, resolve to a
        reject marker (``DISAMBIG``/``LIST``/``CATEGORY``/``CONCEPT``/``UNTYPED``), or
        do not map to a member of :class:`EntityType` (e.g. WordNet-only ``species``
        rows this tier does not carry as ``name_seed``).
    """
    classes = wd_facts.get(qid, {}).get("p31", [])
    hits: set[str] = set()
    for klass in classes:
        hits.update(class_types.get(klass, []))
    if hits & _REJECT_MARKERS:
        return None
    for candidate in _TYPE_PRIORITY:
        if candidate in hits:
            try:
                return EntityType(candidate)
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------------------
# Step 3 -- hypernym and domain hint
# --------------------------------------------------------------------------------------

#: Wikidata P31 label (lowercased) -> the taxonomy leaf it settles, for the entity types
#: whose per-type default is not safe (`organization`, `place`: D-82/D-81, see module
#: docstring) or worth sharpening (`work`, `event`, `other`). Deliberately small: a label
#: this table does not recognise leaves `domain_hint_from_label` returning ``None``
#: rather than guessing, so `tag_domain` decides with no bad prior to fight.
_LABEL_DOMAIN: dict[str, str] = {
    # settlements (D-81)
    "city": DomainTag.NATURE_SETTLEMENTS.value,
    "town": DomainTag.NATURE_SETTLEMENTS.value,
    "village": DomainTag.NATURE_SETTLEMENTS.value,
    "municipality": DomainTag.NATURE_SETTLEMENTS.value,
    "neighbourhood": DomainTag.NATURE_SETTLEMENTS.value,
    "neighborhood": DomainTag.NATURE_SETTLEMENTS.value,
    "hamlet": DomainTag.NATURE_SETTLEMENTS.value,
    "suburb": DomainTag.NATURE_SETTLEMENTS.value,
    "borough": DomainTag.NATURE_SETTLEMENTS.value,
    "township": DomainTag.NATURE_SETTLEMENTS.value,
    "capital city": DomainTag.NATURE_SETTLEMENTS.value,
    "port settlement": DomainTag.NATURE_SETTLEMENTS.value,
    # polities (D-81)
    "sovereign state": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "country": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "state": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "u.s. state": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "province": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "empire": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "historical country": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "kingdom": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "republic": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "emirate": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "confederation": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    "federation": DomainTag.LAW_GOVERNMENT_POLITIES.value,
    # physical geography (unambiguous even without D-81's two new leaves)
    "mountain": DomainTag.NATURE_LANDFORMS.value,
    "river": DomainTag.NATURE_LANDFORMS.value,
    "lake": DomainTag.NATURE_WATER_BODIES.value,
    "sea": DomainTag.NATURE_WATER_BODIES.value,
    "ocean": DomainTag.NATURE_WATER_BODIES.value,
    "island": DomainTag.NATURE_LANDFORMS.value,
    "continent": DomainTag.NATURE_LANDFORMS.value,
    "desert": DomainTag.NATURE_LANDFORMS.value,
    "valley": DomainTag.NATURE_LANDFORMS.value,
    "volcano": DomainTag.NATURE_LANDFORMS.value,
    "glacier": DomainTag.NATURE_LANDFORMS.value,
    "mountain range": DomainTag.NATURE_LANDFORMS.value,
    "national park": DomainTag.NATURE_CONSERVATION.value,
    "protected area": DomainTag.NATURE_CONSERVATION.value,
    "nature reserve": DomainTag.NATURE_CONSERVATION.value,
    # organisations (D-82's specific finding: no safe single default)
    "company": DomainTag.BUSINESS_GENERAL.value,
    "corporation": DomainTag.BUSINESS_GENERAL.value,
    "public company": DomainTag.BUSINESS_GENERAL.value,
    "business": DomainTag.BUSINESS_GENERAL.value,
    "university": DomainTag.EDUCATION_HIGHER_EDUCATION.value,
    "college": DomainTag.EDUCATION_HIGHER_EDUCATION.value,
    "higher education institution": DomainTag.EDUCATION_HIGHER_EDUCATION.value,
    "church": DomainTag.HUMANITIES_RELIGION.value,
    "christian denomination": DomainTag.HUMANITIES_RELIGION.value,
    "religious denomination": DomainTag.HUMANITIES_RELIGION.value,
    "religious organization": DomainTag.HUMANITIES_RELIGION.value,
    "religious order": DomainTag.HUMANITIES_RELIGION.value,
    "diocese": DomainTag.HUMANITIES_RELIGION.value,
    "political party": DomainTag.LAW_GOVERNMENT_ELECTIONS_POLITICS.value,
    "government agency": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "federal agency of the united states": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "ministry": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "executive department": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "intergovernmental organization": DomainTag.LAW_GOVERNMENT_INTERNATIONAL_LAW.value,
    "sports club": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "sports team": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "football club": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "record label": DomainTag.ARTS_MUSIC.value,
    "museum": DomainTag.ARTS_VISUAL_ART.value,
    # works
    "painting": DomainTag.ARTS_VISUAL_ART.value,
    "sculpture": DomainTag.ARTS_VISUAL_ART.value,
    "work of art": DomainTag.ARTS_VISUAL_ART.value,
    "photograph": DomainTag.ARTS_PHOTOGRAPHY.value,
    "film": DomainTag.ARTS_FILM.value,
    "television series": DomainTag.ARTS_FILM.value,
    "television program": DomainTag.ARTS_FILM.value,
    "animated film": DomainTag.ARTS_FILM.value,
    "novel": DomainTag.HUMANITIES_LITERATURE.value,
    "poem": DomainTag.HUMANITIES_LITERATURE.value,
    "play": DomainTag.HUMANITIES_LITERATURE.value,
    "book": DomainTag.HUMANITIES_LITERATURE.value,
    "literary work": DomainTag.HUMANITIES_LITERATURE.value,
    "short story": DomainTag.HUMANITIES_LITERATURE.value,
    "album": DomainTag.ARTS_MUSIC.value,
    "studio album": DomainTag.ARTS_MUSIC.value,
    "song": DomainTag.ARTS_MUSIC.value,
    "single": DomainTag.ARTS_MUSIC.value,
    "opera": DomainTag.ARTS_MUSIC.value,
    "symphony": DomainTag.ARTS_MUSIC.value,
    "musical composition": DomainTag.ARTS_MUSIC.value,
    "ballet": DomainTag.ARTS_DANCE.value,
    "building": DomainTag.ARTS_ARCHITECTURE.value,
    "skyscraper": DomainTag.ARTS_ARCHITECTURE.value,
    "monument": DomainTag.ARTS_ARCHITECTURE.value,
    # events
    "election": DomainTag.LAW_GOVERNMENT_ELECTIONS_POLITICS.value,
    "referendum": DomainTag.LAW_GOVERNMENT_ELECTIONS_POLITICS.value,
    "olympic games": DomainTag.SPORTS_RECREATION_GENERAL.value,
    "sports competition": DomainTag.SPORTS_RECREATION_GENERAL.value,
    "sporting event": DomainTag.SPORTS_RECREATION_GENERAL.value,
    "natural disaster": DomainTag.NATURE_NATURAL_DISASTERS.value,
    "earthquake": DomainTag.NATURE_NATURAL_DISASTERS.value,
    "hurricane": DomainTag.NATURE_NATURAL_DISASTERS.value,
    "council of the catholic church": DomainTag.HUMANITIES_RELIGION.value,
    # other
    "treaty": DomainTag.LAW_GOVERNMENT_INTERNATIONAL_LAW.value,
    "currency": DomainTag.BUSINESS_FINANCE.value,
    "language": DomainTag.LANGUAGE_GENERAL.value,
    "deity": DomainTag.HUMANITIES_MYTHOLOGY.value,
    "mythological figure": DomainTag.HUMANITIES_MYTHOLOGY.value,
}

#: Occupation label (lowercased) -> domain, for refining `person`'s default
#: (`history.historical_figures`). `wd_facts.json` has no `P106`; fetched separately
#: (`load_or_fetch_occupations`) only for `person`-typed `name_seed` rows.
_OCCUPATION_DOMAIN: dict[str, str] = {
    "physicist": DomainTag.SCIENCE_GENERAL.value,
    "chemist": DomainTag.SCIENCE_GENERAL.value,
    "biologist": DomainTag.SCIENCE_GENERAL.value,
    "mathematician": DomainTag.SCIENCE_GENERAL.value,
    "astronomer": DomainTag.SCIENCE_GENERAL.value,
    "geologist": DomainTag.SCIENCE_GENERAL.value,
    "computer scientist": DomainTag.SCIENCE_GENERAL.value,
    "scientist": DomainTag.SCIENCE_GENERAL.value,
    "engineer": DomainTag.SCIENCE_GENERAL.value,
    "inventor": DomainTag.SCIENCE_GENERAL.value,
    "painter": DomainTag.ARTS_VISUAL_ART.value,
    "sculptor": DomainTag.ARTS_VISUAL_ART.value,
    "photographer": DomainTag.ARTS_PHOTOGRAPHY.value,
    "visual artist": DomainTag.ARTS_VISUAL_ART.value,
    "architect": DomainTag.ARTS_ARCHITECTURE.value,
    "writer": DomainTag.HUMANITIES_LITERATURE.value,
    "novelist": DomainTag.HUMANITIES_LITERATURE.value,
    "poet": DomainTag.HUMANITIES_LITERATURE.value,
    "playwright": DomainTag.HUMANITIES_LITERATURE.value,
    "journalist": DomainTag.HUMANITIES_LITERATURE.value,
    "essayist": DomainTag.HUMANITIES_LITERATURE.value,
    "singer": DomainTag.ARTS_MUSIC.value,
    "singer-songwriter": DomainTag.ARTS_MUSIC.value,
    "musician": DomainTag.ARTS_MUSIC.value,
    "composer": DomainTag.ARTS_MUSIC.value,
    "songwriter": DomainTag.ARTS_MUSIC.value,
    "rapper": DomainTag.ARTS_MUSIC.value,
    "pianist": DomainTag.ARTS_MUSIC.value,
    "actor": DomainTag.ARTS_FILM.value,
    "film actor": DomainTag.ARTS_FILM.value,
    "film director": DomainTag.ARTS_FILM.value,
    "film producer": DomainTag.ARTS_FILM.value,
    "screenwriter": DomainTag.ARTS_FILM.value,
    "athlete": DomainTag.SPORTS_RECREATION_GENERAL.value,
    "association football player": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "basketball player": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "baseball player": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "american football player": DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    "tennis player": DomainTag.SPORTS_RECREATION_INDIVIDUAL_SPORTS.value,
    "golfer": DomainTag.SPORTS_RECREATION_INDIVIDUAL_SPORTS.value,
    "boxer": DomainTag.SPORTS_RECREATION_COMBAT_SPORTS.value,
    "swimmer": DomainTag.SPORTS_RECREATION_WATER_SPORTS.value,
    "politician": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "statesperson": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "diplomat": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "monarch": DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    "judge": DomainTag.LAW_GOVERNMENT_COURTS_JUSTICE.value,
    "lawyer": DomainTag.LAW_GOVERNMENT_GENERAL.value,
    "priest": DomainTag.HUMANITIES_RELIGION.value,
    "bishop": DomainTag.HUMANITIES_RELIGION.value,
    "pope": DomainTag.HUMANITIES_RELIGION.value,
    "imam": DomainTag.HUMANITIES_RELIGION.value,
    "rabbi": DomainTag.HUMANITIES_RELIGION.value,
    "monk": DomainTag.HUMANITIES_RELIGION.value,
    "theologian": DomainTag.HUMANITIES_RELIGION.value,
    "clergyman": DomainTag.HUMANITIES_RELIGION.value,
    "philosopher": DomainTag.HUMANITIES_PHILOSOPHY.value,
    "historian": DomainTag.HISTORY_GENERAL.value,
    "economist": DomainTag.BUSINESS_GENERAL.value,
    "explorer": DomainTag.HISTORY_EXPLORATION_COLONIZATION.value,
    "military officer": DomainTag.HISTORY_GENERAL.value,
    "general": DomainTag.HISTORY_GENERAL.value,
}

#: Labels a P31/occupation fetch legitimately returns that are too generic to be a
#: hypernym ("entity", "human" and the like tell the reader nothing WordNet's own
#: instance hypernym wouldn't already say better) -- excluded from
#: `hypernym_from_label` so the fallback (`seed_list.hypernym_for`) runs instead.
_HYPERNYM_STOPLIST = frozenset({"entity", "human", "type", "concept", "occurrence"})


def hypernym_from_label(labels: list[str]) -> str | None:
    """Return the first usable Wikidata P31 label as a hypernym, or ``None``.

    Args:
        labels: This row's P31 class labels, most-specific first (as
            `label_summary` orders them -- Wikidata's own P31 order, not a ranking).

    Returns:
        The first label not in :data:`_HYPERNYM_STOPLIST`, lowercased, or ``None`` when
        every label is generic or there are none.
    """
    for label in labels:
        normalized = label.strip().lower()
        if normalized and normalized not in _HYPERNYM_STOPLIST:
            return normalized
    return None


def domain_hint_from_label(labels: list[str]) -> str | None:
    """Return the first :data:`_LABEL_DOMAIN` match among a row's P31 labels, or ``None``."""
    for label in labels:
        domain = _LABEL_DOMAIN.get(label.strip().lower())
        if domain:
            return domain
    return None


#: Priority among the domains :data:`_OCCUPATION_DOMAIN` can return, checked in this
#: order rather than trusting P106's own list order -- Wikidata does not list a
#: person's occupations by prominence (observed live 2026-09-08: John F. Kennedy's
#: P106 list is ``["writer", "politician", "statesperson", "journalist", "naval
#: officer", "anti-communist"]``, "writer" first; Harry S. Truman's is ``["judge",
#: "captain", "businessperson", "politician", ...]``, "judge" first). Holding office is
#: the least deniable, most defining fact this table can see about a person, so
#: government beats the arts/science/sports occupations that follow it; those, in turn,
#: beat the generic law/history/business ones that would otherwise fire on almost any
#: public figure's incidental "lawyer" or "businessperson" line.
_OCCUPATION_DOMAIN_PRIORITY: tuple[str, ...] = (
    DomainTag.LAW_GOVERNMENT_GOVERNMENT_STRUCTURE.value,
    DomainTag.HUMANITIES_RELIGION.value,
    DomainTag.SCIENCE_GENERAL.value,
    DomainTag.ARTS_VISUAL_ART.value,
    DomainTag.ARTS_PHOTOGRAPHY.value,
    DomainTag.ARTS_ARCHITECTURE.value,
    DomainTag.HUMANITIES_LITERATURE.value,
    DomainTag.ARTS_MUSIC.value,
    DomainTag.ARTS_FILM.value,
    DomainTag.SPORTS_RECREATION_TEAM_SPORTS.value,
    DomainTag.SPORTS_RECREATION_INDIVIDUAL_SPORTS.value,
    DomainTag.SPORTS_RECREATION_COMBAT_SPORTS.value,
    DomainTag.SPORTS_RECREATION_WATER_SPORTS.value,
    DomainTag.SPORTS_RECREATION_GENERAL.value,
    DomainTag.HUMANITIES_PHILOSOPHY.value,
    DomainTag.HISTORY_EXPLORATION_COLONIZATION.value,
    DomainTag.LAW_GOVERNMENT_COURTS_JUSTICE.value,
    DomainTag.LAW_GOVERNMENT_GENERAL.value,
    DomainTag.HISTORY_GENERAL.value,
    DomainTag.BUSINESS_GENERAL.value,
)


def occupation_domain(occupation_labels: list[str]) -> str | None:
    """Return the highest-priority :data:`_OCCUPATION_DOMAIN` match among occupations.

    Scans by :data:`_OCCUPATION_DOMAIN_PRIORITY`, not by the order ``occupation_labels``
    happens to list its occupations in -- see that data's docstring for why list order
    is not usable.
    """
    found = {
        domain
        for label in occupation_labels
        if (domain := _OCCUPATION_DOMAIN.get(label.strip().lower()))
    }
    for domain in _OCCUPATION_DOMAIN_PRIORITY:
        if domain in found:
            return domain
    return None


def event_domain(headword: str, labels: list[str]) -> str | None:
    """Return a refined domain for an ``event`` row, or ``None`` for the type default.

    A battle or war is dated toward ``history.world_wars`` when a year in the headword
    falls in 1914-1945, else ``history.modern_history``; every other recognised event
    label is looked up in :data:`_LABEL_DOMAIN` directly.
    """
    normalized = [label.strip().lower() for label in labels]
    war_labels = {"battle", "war", "military conflict", "military operation"}
    if any(label in war_labels for label in normalized):
        year_match = _YEAR.search(headword)
        if year_match and int(year_match.group(1)) in _WORLD_WAR_YEARS:
            return DomainTag.HISTORY_WORLD_WARS.value
        return DomainTag.HISTORY_MODERN_HISTORY.value
    return domain_hint_from_label(labels)


def p31_labels_for(
    qid: str, wd_facts: dict[str, Any], class_labels: dict[str, str], *, limit: int = 3
) -> list[str]:
    """Return up to ``limit`` English labels for a candidate's own P31 classes.

    Args:
        qid: The candidate's own Wikidata id.
        wd_facts: Parsed ``wd_facts.json``.
        class_labels: ``class QID -> label`` (`load_or_fetch_labels`).
        limit: How many labels to return, in Wikidata's own P31 order (not a ranking).
    """
    classes = wd_facts.get(qid, {}).get("p31", [])
    labels = [class_labels[c] for c in classes if c in class_labels]
    return labels[:limit]


# --------------------------------------------------------------------------------------
# Step 4 -- hypernym/domain assembly for one name_seed row
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class TypedHints:
    """Everything derived for one row before the final hypernym/domain decision."""

    entity_type_final: EntityType | None
    type_source: str
    dropped_reason: str
    p31_labels: list[str]
    occupation_labels: list[str] = field(default_factory=list)


def resolve_hypernym_and_domain(*, headword: str, notes: str, hints: TypedHints) -> tuple[str, str]:
    """Return ``(hypernym, domain_hint)`` for one retyped ``name_seed`` row.

    Args:
        headword: The disambiguator-stripped headword.
        notes: The row's original ``notes`` cell (for `seed_list.hypernym_for`'s
            ``us_list`` refinement, the fallback tier).
        hints: This row's type verdict and Wikidata signals.

    Returns:
        ``("", "")`` for a dropped row -- there is no entity left to describe.
    """
    entity_type = hints.entity_type_final
    if entity_type is None:
        return "", ""

    hypernym = hypernym_from_label(hints.p31_labels) or hypernym_for(entity_type, notes)

    if entity_type is EntityType.ORGANIZATION or entity_type is EntityType.PLACE:
        # D-82/D-81: no per-type default is safe for either (see module docstring).
        domain = domain_hint_from_label(hints.p31_labels) or ""
    elif entity_type is EntityType.EVENT:
        domain = event_domain(headword, hints.p31_labels) or DomainTag.HISTORY_GENERAL.value
    elif entity_type is EntityType.PERSON:
        domain = (
            domain_hint_from_label(hints.p31_labels)
            or occupation_domain(hints.occupation_labels)
            or DomainTag.HISTORY_HISTORICAL_FIGURES.value
        )
    elif entity_type is EntityType.WORK:
        domain = domain_hint_from_label(hints.p31_labels) or DomainTag.ARTS_GENERAL.value
    elif entity_type is EntityType.OTHER:
        domain = domain_hint_from_label(hints.p31_labels) or DomainTag.HUMANITIES_GENERAL.value
    else:
        # PRODUCT, SPECIES: rare-to-absent among name_seed rows; the old per-type
        # default (business.general / nature.animals) is not the demonstrated failure
        # mode D-82 found, so it is kept rather than blanked.
        domain = domain_hint_from_label(hints.p31_labels) or domain_hint_for(entity_type, notes)

    return hypernym, domain


# --------------------------------------------------------------------------------------
# Step 2 -- the model half of retyping
# --------------------------------------------------------------------------------------


class RetypeLabel(StrEnum):
    """The strict-enum verdict a retype call may return.

    :class:`EntityType`'s own members, plus the one sentinel this call alone needs:
    a headword Wikipedia capitalises that names no specific thing (D-82's *Arianism*/
    *NGC 6302*/*Mil Mi-24* damaged-gloss cases were all a wrong *type*, not this --
    "not a named entity" is for the row that should never have reached this pipeline at
    all, not for one this pipeline mistyped).
    """

    PERSON = EntityType.PERSON.value
    PLACE = EntityType.PLACE.value
    ORGANIZATION = EntityType.ORGANIZATION.value
    WORK = EntityType.WORK.value
    EVENT = EntityType.EVENT.value
    PRODUCT = EntityType.PRODUCT.value
    SPECIES = EntityType.SPECIES.value
    OTHER = EntityType.OTHER.value
    NOT_A_NAMED_ENTITY = NOT_A_NAMED_ENTITY


class RetypeVerdict(BaseModel):
    """One name's retype verdict, echoed back by position (D-18's own convention)."""

    model_config = ConfigDict(extra="forbid")

    term: str = PydanticField(min_length=1)
    entity_type: RetypeLabel


class RetypeBatch(BaseModel):
    """Verdicts for one batch of `name_seed` rows, in the order given."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[RetypeVerdict] = PydanticField(min_length=1, max_length=RETYPE_BATCH_SIZE)


RETYPE_INSTRUCTIONS = """You are retyping named-entity candidates for an English dictionary's \
proper-noun entries. Each candidate carries a type a cheap, sometimes-wrong pipeline already \
assigned; you are the check on it.

For each name, decide what it actually names:

- person: an individual, real or fictional (a mythological or literary character counts);
- place: a settlement, region, country, landform, or other location;
- organization: a company, government body, religious body, sports team, or other group;
- work: a book, film, song, painting, or other named creative or intellectual work;
- event: a war, election, disaster, or other named happening;
- product: a commercial product or brand distinct from the company that makes it;
- species: a taxon (a kind of organism), not an individual organism;
- other: a real named thing that fits none of the above (a language, a treaty, a \
programming language, a chemical element);
- not_a_named_entity: an ordinary English word or phrase Wikipedia happens to capitalise \
(a doctrine, a common noun, a disambiguation artifact) rather than the name of one specific \
thing.

You are given each name's current type, and Wikidata's own classification and vital-article \
topic when available -- both are hints, not verdicts; use your own knowledge of the name \
over either when they conflict, the same way you would correct a wrong caption. Return a \
verdict for every name you are given, in the order given, echoing the name exactly as it \
was written."""


def build_retype_prompt(items: list[dict[str, str]]) -> str:
    """Return the volatile half of the retype prompt for one batch.

    Args:
        items: Per-name context dicts with keys ``name``, ``tsv_type``, ``wikidata``,
            ``disambiguator``, ``vital`` (any may be ``""``).
    """
    lines = []
    for i, item in enumerate(items):
        bits = [f"tsv_type={item['tsv_type']}"]
        if item["wikidata"]:
            bits.append(f"wikidata_class={item['wikidata']}")
        if item["disambiguator"]:
            bits.append(f"disambiguator={item['disambiguator']}")
        if item["vital"]:
            bits.append(f"vital_topic={item['vital']}")
        lines.append(f"  {i + 1}. {item['name']} — {'; '.join(bits)}")
    return f"Names ({len(items)}):\n" + "\n".join(lines)


@dataclass(slots=True)
class CostTracker:
    """A minimal stand-in for `budget.BudgetGuard`/`CostMeter`, for one process's run.

    A one-off script has no ledger and no concurrent-run accounting to protect, so it
    tracks one number under one lock rather than reserving-then-reconciling: the run is
    short and single-purpose enough that the failure mode a reservation scheme guards
    against (many concurrent workers all starting a call the budget cannot actually
    cover) is adequately handled by checking the running total before every dispatch.
    """

    budget_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def has_room(self) -> bool:
        """Return whether another call should be dispatched."""
        async with self._lock:
            return self.spent_usd < self.budget_usd

    async def record(
        self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int
    ) -> float:
        """Price and record one call's usage; return its cost."""
        cost = estimate_cost(
            RETYPE_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            tier=ServiceTier.FLEX,
        ).total_usd
        async with self._lock:
            self.spent_usd += cost
            self.calls += 1
            self.input_tokens += input_tokens
            self.cached_input_tokens += cached_input_tokens
            self.output_tokens += output_tokens
        return cost


def build_retype_agent() -> Agent[None, RetypeBatch]:
    """Build the one `pydantic_ai` agent every retype batch shares."""
    return Agent(
        infer_model(f"openai:{RETYPE_MODEL}"),
        output_type=NativeOutput(RetypeBatch, strict=True),
        instructions=RETYPE_INSTRUCTIONS,
        retries=0,
    )


_RETYPE_SETTINGS = OpenAIResponsesModelSettings(
    max_tokens=RETYPE_MAX_TOKENS,
    openai_service_tier=ServiceTier.FLEX.value,
    openai_prompt_cache_key=RETYPE_PROMPT_CACHE_KEY,
    openai_reasoning_effort="none",
)


async def run_retype_batch(
    agent: Agent[None, RetypeBatch], items: list[dict[str, str]], tracker: CostTracker
) -> dict[str, RetypeLabel] | None:
    """Run one retype batch and return ``{name: verdict}``, or ``None`` on failure.

    Two attempts: a real API call is worth one retry on a transient failure, and this
    script's own caller already treats a lost batch as "keep the TSV's type, unchanged"
    rather than a fatal error (see module docstring, step 2) -- a one-off script over a
    TSV should degrade a batch at a time, not abort a two-hour run over one hiccup.
    """
    prompt = build_retype_prompt(items)
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            result = await agent.run(prompt, model_settings=_RETYPE_SETTINGS)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(2.0)
            continue
        usage = result.usage
        await tracker.record(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=getattr(usage, "cache_read_tokens", 0) or 0,
        )
        return {v.term.strip(): v.entity_type for v in result.output.verdicts}
    sys.stderr.write(f"warning: retype batch of {len(items)} failed twice: {last_error}\n")
    return None


async def run_retype(
    rows: list[CandidateRow],
    *,
    wd_facts: dict[str, Any],
    vital_all: dict[str, Any],
    class_labels: dict[str, str],
    tracker: CostTracker,
    batch_size: int,
    concurrency: int,
) -> dict[int, RetypeLabel]:
    """Run every `name_seed` row's retype call and return ``{row.index: verdict}``.

    Rows are batched in TSV order (matching `retrofit._classify_kind_pass`'s own
    "sort before batching" reasoning: the same input always produces the same batches,
    whatever order concurrent work finishes decorating rows in). Dispatch stops taking
    on new batches once the budget is spent; already-inflight batches are allowed to
    finish rather than cancelled, so a budget that runs out mid-run never wastes the
    calls it already paid for.
    """
    batches = [rows[start : start + batch_size] for start in range(0, len(rows), batch_size)]
    agent = build_retype_agent()
    semaphore = asyncio.Semaphore(concurrency)
    verdicts: dict[int, RetypeLabel] = {}

    async def run_one(batch: list[CandidateRow]) -> None:
        if not await tracker.has_room():
            return
        items = []
        for row in batch:
            qid = row.cells.get("qid", "")
            labels = p31_labels_for(qid, wd_facts, class_labels) if qid else []
            # The vital-article cache is keyed by the raw Wikipedia title (disambiguator
            # and all), not the stripped headword -- only `vital_all`'s own lookup uses
            # the un-stripped `name` cell.
            vital = vital_all.get(row.cells["name"], {})
            items.append(
                {
                    "name": row.cells["headword"],
                    "tsv_type": row.cells.get("entity_type", ""),
                    "wikidata": "|".join(labels),
                    "disambiguator": row.cells.get("disambiguator", ""),
                    "vital": f"{vital.get('topic', '')}/{vital.get('section', '')}".strip("/"),
                }
            )
        async with semaphore:
            if not await tracker.has_room():
                return
            result = await run_retype_batch(agent, items, tracker)
        if result is None:
            return
        for row in batch:
            verdict = result.get(row.cells["headword"].strip())
            if verdict is not None:
                verdicts[row.index] = verdict

    await asyncio.gather(*(run_one(batch) for batch in batches))
    return verdicts


# --------------------------------------------------------------------------------------
# Collision notes (step 1, continued)
# --------------------------------------------------------------------------------------


def annotate_collisions(rows: list[CandidateRow], store: LexemeStore) -> None:
    """Append a `headword_collision` note to every row whose stripped slug collides.

    Two kinds, both kept rather than dropped (module docstring, step 1):

    * the slug is already a live entry in the store;
    * two rows in this file produce the same slug (most often the same underlying
      entity listed under two disambiguators, or a genuine same-name coincidence).

    Mutates each row's ``notes`` cell in place.
    """
    slug_to_rows: dict[str, list[CandidateRow]] = {}
    for row in rows:
        slug = row.cells.get("_slug", "")
        if slug:
            slug_to_rows.setdefault(slug, []).append(row)

    for row in rows:
        slug = row.cells.get("_slug", "")
        if not slug:
            continue
        extra_notes: list[str] = []
        if store.exists(slug):
            extra_notes.append(f"headword_collision: store has '{slug}'")
        siblings = [r for r in slug_to_rows.get(slug, []) if r is not row]
        for sibling in siblings:
            extra_notes.append(
                f"headword_collision: also produced by '{sibling.cells['name']}' "
                f"(row {sibling.index})"
            )
        if extra_notes:
            existing = row.cells.get("notes", "")
            row.cells["notes"] = (
                "; ".join([existing, *extra_notes]) if existing else "; ".join(extra_notes)
            )


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RetypeDisagreement:
    """One row whose entity type changed, for the D-83 report table."""

    name: str
    old: str
    new: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=Path("data/core/tier6_candidates.tsv"))
    parser.add_argument("--cache", type=Path, default=Path("data/core/tier6_cache"))
    parser.add_argument("--store", type=Path, default=Path("data/core-store"))
    parser.add_argument("--out", type=Path, default=Path("data/core/tier6_seeds.tsv"))
    parser.add_argument("--budget", type=float, default=2.00)
    parser.add_argument("--batch-size", type=int, default=RETYPE_BATCH_SIZE)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip every Wikidata fetch and reuse whatever the cache already has.",
    )
    parser.add_argument(
        "--no-occupations",
        action="store_true",
        help="Skip the P106 occupation fetch (WDQS); person rows keep the plain default.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap name_seed rows, for a dry run."
    )
    return parser.parse_args(argv)


@dataclass(slots=True)
class WikidataContext:
    """The free Wikidata-derived signal every `name_seed` row can draw on."""

    wd_facts: dict[str, Any]
    class_types: dict[str, list[str]]
    vital_all: dict[str, Any]
    class_labels: dict[str, str]
    occupations: dict[str, list[str]]
    occupation_labels: dict[str, str]


@dataclass(slots=True)
class RetypeOutcome:
    """The per-row bookkeeping `apply_retype_results` collects across all rows."""

    disagreements: list[RetypeDisagreement] = field(default_factory=list)
    agreements: int = 0
    dropped_counts: Counter[str] = field(default_factory=Counter)
    type_source_counts: Counter[str] = field(default_factory=Counter)


def prepare_headwords(rows: list[CandidateRow], store: LexemeStore) -> None:
    """Run step 1 (disambiguator stripping + collision notes) over every row."""
    for row in rows:
        headword, disambiguator = strip_disambiguator(row.cells["name"])
        row.cells["headword"] = headword
        row.cells["disambiguator"] = disambiguator
        try:
            row.cells["_slug"] = slugify(headword)
        except ValueError:
            row.cells["_slug"] = ""
    annotate_collisions(rows, store)


def load_wikidata_context(
    cache_dir: Path,
    name_seed_rows: list[CandidateRow],
    *,
    offline: bool,
    fetch_occupations_flag: bool,
) -> WikidataContext:
    """Load the builder's own cache and fetch (or reuse) this script's own two caches."""
    wd_facts = load_cache_json(cache_dir, "wd_facts.json")
    class_types = load_cache_json(cache_dir, "class_types.json")
    vital_all = load_cache_json(cache_dir, "vital_all.json")

    class_qids: set[str] = set()
    for row in name_seed_rows:
        qid = row.cells.get("qid", "")
        class_qids.update(wd_facts.get(qid, {}).get("p31", []))
    class_labels = load_or_fetch_labels(cache_dir, "class_labels.json", class_qids, offline=offline)

    person_qids = [
        row.cells["qid"]
        for row in name_seed_rows
        if row.cells.get("entity_type") == EntityType.PERSON.value and row.cells.get("qid")
    ]
    occupations: dict[str, list[str]] = {}
    occupation_labels: dict[str, str] = {}
    if fetch_occupations_flag:
        occupations = load_or_fetch_occupations(cache_dir, person_qids, offline=offline)
        occupation_qids: set[str] = set()
        for occ_list in occupations.values():
            occupation_qids.update(occ_list)
        occupation_labels = load_or_fetch_labels(
            cache_dir, "occupation_labels.json", occupation_qids, offline=offline
        )
    return WikidataContext(
        wd_facts=wd_facts,
        class_types=class_types,
        vital_all=vital_all,
        class_labels=class_labels,
        occupations=occupations,
        occupation_labels=occupation_labels,
    )


def apply_living_and_recent(rows: list[CandidateRow], wd_facts: dict[str, Any]) -> None:
    """Append step 4's `living_person`/`recent_creation` tokens to each row's notes."""
    for row in rows:
        tokens = living_and_recent_notes(wd_facts.get(row.cells.get("qid", ""), {}))
        if not tokens:
            continue
        existing = row.cells.get("notes", "")
        row.cells["notes"] = "; ".join([existing, *tokens]) if existing else "; ".join(tokens)


def _decide_type(
    row: CandidateRow, verdict: RetypeLabel | None, ctx: WikidataContext, outcome: RetypeOutcome
) -> tuple[EntityType | None, str, str]:
    """Return ``(entity_type_final, type_source, dropped_reason)`` for one row.

    Split out of :func:`apply_retype_results` so that function's own branch count stays
    readable; this is the one place step 2's three-way ``type_source`` decision (module
    docstring) is made.
    """
    tsv_type_raw = row.cells.get("entity_type", "")
    qid = row.cells.get("qid", "")

    if verdict is None:
        # No verdict (batch dropped, or this row's echo did not match): keep the TSV's
        # type unchanged rather than guess.
        try:
            return EntityType(tsv_type_raw), "tsv", ""
        except ValueError:
            return None, "tsv", ""

    if verdict is RetypeLabel.NOT_A_NAMED_ENTITY:
        outcome.dropped_counts[NOT_A_NAMED_ENTITY] += 1
        return None, "verdict", NOT_A_NAMED_ENTITY

    entity_type_final = EntityType(verdict.value)
    if verdict.value != tsv_type_raw:
        outcome.disagreements.append(
            RetypeDisagreement(name=row.cells["headword"], old=tsv_type_raw, new=verdict.value)
        )
        return entity_type_final, "verdict", ""

    outcome.agreements += 1
    wikidata_type = candidate_wikidata_type(qid, ctx.wd_facts, ctx.class_types) if qid else None
    type_source = "wikidata" if wikidata_type is entity_type_final else "tsv"
    return entity_type_final, type_source, ""


def apply_retype_results(
    name_seed_rows: list[CandidateRow], verdicts: dict[int, RetypeLabel], ctx: WikidataContext
) -> RetypeOutcome:
    """Write step 2/3's columns onto every `name_seed` row; return the run's tallies."""
    outcome = RetypeOutcome()
    for row in name_seed_rows:
        qid = row.cells.get("qid", "")
        entity_type_final, type_source, dropped_reason = _decide_type(
            row, verdicts.get(row.index), ctx, outcome
        )
        outcome.type_source_counts[type_source] += 1

        p31_labels = p31_labels_for(qid, ctx.wd_facts, ctx.class_labels) if qid else []
        occ_labels = [
            ctx.occupation_labels[o]
            for o in ctx.occupations.get(qid, [])
            if o in ctx.occupation_labels
        ]
        hints = TypedHints(
            entity_type_final=entity_type_final,
            type_source=type_source,
            dropped_reason=dropped_reason,
            p31_labels=p31_labels,
            occupation_labels=occ_labels,
        )
        hypernym, domain_hint = resolve_hypernym_and_domain(
            headword=row.cells["headword"], notes=row.cells.get("notes", ""), hints=hints
        )

        row.cells["entity_type_final"] = (
            entity_type_final.value if entity_type_final is not None else NOT_A_NAMED_ENTITY
        )
        row.cells["type_source"] = type_source
        row.cells["hypernym"] = hypernym
        row.cells["domain_hint"] = domain_hint
        row.cells["dropped_reason"] = dropped_reason
        # `seed_list.read_seed_list` compatibility: overwrite in place (module docstring).
        row.cells["name"] = row.cells["headword"]
        row.cells["entity_type"] = row.cells["entity_type_final"]
    return outcome


def finalize_wordnet_rows(rows: list[CandidateRow], name_seed_index: set[int]) -> None:
    """Headword/disambiguator only for `wordnet` rows; everything else stays as-is."""
    for row in rows:
        if row.index in name_seed_index or row.cells.get("source") != "wordnet":
            continue
        row.cells["entity_type_final"] = row.cells.get("entity_type", "")
        row.cells["type_source"] = "tsv"
        row.cells["name"] = row.cells["headword"]


def build_report(
    *,
    rows: list[CandidateRow],
    name_seed_rows: list[CandidateRow],
    outcome: RetypeOutcome,
    tracker: CostTracker,
    out_path: Path,
) -> dict[str, Any]:
    """Assemble the JSON report `main` prints to stdout."""
    disambiguators_stripped = sum(1 for r in rows if r.cells.get("disambiguator"))
    kept = sum(1 for r in name_seed_rows if r.cells["dropped_reason"] == "")
    denominator = max(len(name_seed_rows), 1)
    hypernym_coverage = sum(1 for r in name_seed_rows if r.cells["hypernym"]) / denominator
    domain_coverage = sum(1 for r in name_seed_rows if r.cells["domain_hint"]) / denominator
    typed = len(name_seed_rows) - outcome.dropped_counts.get(NOT_A_NAMED_ENTITY, 0)

    return {
        "candidates_in": len(rows),
        "name_seed_rows": len(name_seed_rows),
        "wordnet_rows": sum(1 for r in rows if r.cells.get("source") == "wordnet"),
        "kept": kept,
        "dropped": dict(outcome.dropped_counts),
        "disambiguators_stripped": disambiguators_stripped,
        "type_source_counts": dict(outcome.type_source_counts),
        "agreement_rate": outcome.agreements / max(typed, 1),
        "disagreements": len(outcome.disagreements),
        "hypernym_coverage": round(hypernym_coverage, 4),
        "domain_hint_coverage": round(domain_coverage, 4),
        "cost_usd": round(tracker.spent_usd, 6),
        "calls": tracker.calls,
        "input_tokens": tracker.input_tokens,
        "cached_input_tokens": tracker.cached_input_tokens,
        "output_tokens": tracker.output_tokens,
        "disagreement_table": [
            {"name": d.name, "old": d.old, "new": d.new} for d in outcome.disagreements[:60]
        ],
        "output": str(out_path),
    }


def main(argv: list[str] | None = None) -> int:
    """Clean `tier6_candidates.tsv` into `tier6_seeds.tsv`; print a JSON report."""
    args = parse_args(argv)

    header, rows = read_tsv(args.candidates)
    output_header = build_output_header(header)
    for column in NEW_COLUMNS:
        for row in rows:
            row.cells.setdefault(column, "")

    store = LexemeStore(StoreConfig(root=args.store))
    prepare_headwords(rows, store)

    name_seed_rows = [r for r in rows if r.cells.get("source") == NAME_SEED_SOURCE]
    if args.limit is not None:
        name_seed_rows = name_seed_rows[: args.limit]
    name_seed_index = {r.index for r in name_seed_rows}

    ctx = load_wikidata_context(
        args.cache,
        name_seed_rows,
        offline=args.offline,
        fetch_occupations_flag=not args.no_occupations,
    )
    apply_living_and_recent(name_seed_rows, ctx.wd_facts)

    tracker = CostTracker(budget_usd=args.budget)
    verdicts = asyncio.run(
        run_retype(
            name_seed_rows,
            wd_facts=ctx.wd_facts,
            vital_all=ctx.vital_all,
            class_labels=ctx.class_labels,
            tracker=tracker,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
        )
    )

    outcome = apply_retype_results(name_seed_rows, verdicts, ctx)
    finalize_wordnet_rows(rows, name_seed_index)

    for row in rows:
        row.cells.pop("_slug", None)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(args.out, output_header, rows)

    report = build_report(
        rows=rows,
        name_seed_rows=name_seed_rows,
        outcome=outcome,
        tracker=tracker,
        out_path=args.out,
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
