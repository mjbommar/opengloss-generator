"""Row projection for the v2.0 Hugging Face release: one stored entry in, many rows out.

Everything here is pure and offline. :class:`RowBuilder` walks one
:class:`~opengloss_generator.schema.Lexeme` at a time and yields
``(repo_slug, config_name, row)`` triples matching the explicit schemas in
:mod:`opengloss_generator.export.hf_schemas`, while folding the same entry into
:class:`Stats` — so the numbers a dataset card prints are counted from the rows that were
actually written, never typed by hand.

Two things cannot be answered from one entry alone and are therefore deferred:

* a **contrast** names a far sense that usually lives in another entry, and the flat
  contrasts repo carries the far end's headword and canonical gloss so a row reads
  standalone. Contrast rows are buffered during the pass and resolved by
  :meth:`RowBuilder.finish` against the gloss index the same pass built;
* the **tombstoned** relation edges are not in ``Sense.relations`` at all — they were
  removed from it. They are recovered by parsing the removal records the free reconcile
  pass wrote into the entry's provenance table (D-65, D-68), whose line prefixes are
  imported from that module rather than restated here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opengloss_generator import spans
from opengloss_generator.identity import edge_id as make_edge_id
from opengloss_generator.identity import slugify
from opengloss_generator.log import get_logger
from opengloss_generator.schema import (
    ReadingLevel,
    Register,
    RelationType,
    StageName,
    upos_for,
)
from opengloss_generator.taxonomy import root_of
from opengloss_generator.workflows.relation_reconcile import (
    CAP_LINE_PREFIX,
    CAP_RECORD_PREFIX,
    DEDUP_LINE_PREFIX,
    DEDUP_RECORD_PREFIX,
    TOMBSTONE_LINE_PREFIX,
    TOMBSTONE_RECORD_PREFIX,
)

_LOG = get_logger(__name__)

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterator
    from pathlib import Path

    from opengloss_generator.schema import (
        Example,
        Lexeme,
        POSEntry,
        Rendition,
        Sense,
    )

__all__ = [
    "COVERAGE_FEATURES",
    "TIERS",
    "TIER_CORE",
    "TIER_DESCRIPTIONS",
    "TIER_TIER2",
    "TIER_TIER3",
    "TIER_TIER4",
    "TIER_UNKNOWN",
    "CoverageFeature",
    "RowBuilder",
    "Stats",
    "TierIndex",
    "truncate_note",
]

TIER_CORE = "core"
TIER_TIER2 = "tier2"
TIER_TIER3 = "tier3"
#: Stopwords, plus compounds and names at Wikipedia frequency >= 10 (D-75). The source
#: TSV's own ``group`` column (``stopword`` vs. ``wf10``) is not surfaced as a distinct
#: tier — both collapse into ``tier4`` here, per the release's own tier granularity.
TIER_TIER4 = "tier4"
#: Assigned to an entry that is in the store but on none of the rank lists. It is a real
#: value in the exported data rather than a null, so a consumer filtering by tier never
#: silently loses rows.
TIER_UNKNOWN = "unknown"

#: Tiers in coverage-table order.
TIERS: tuple[str, ...] = (TIER_CORE, TIER_TIER2, TIER_TIER3, TIER_TIER4, TIER_UNKNOWN)

#: One line saying what each tier is, for the card's "By tier" section (D-75). Keyed so a
#: tier absent from a given export is simply not looked up, rather than needing its own
#: conditional at the call site.
TIER_DESCRIPTIONS: dict[str, str] = {
    TIER_CORE: "top 10K by composite frequency",
    TIER_TIER2: "ranks to ~42K",
    TIER_TIER3: "the rest of the frequency-ranked single words",
    TIER_TIER4: "stopwords, plus compounds and names at Wikipedia frequency ≥ 10",
}

#: The rank lists under ``data/core/``, in precedence order: an entry is assigned the
#: first tier whose list names it. A file missing from disk is not an error (D-75) — see
#: :meth:`TierIndex.from_dir`.
TIER_FILES: tuple[tuple[str, str], ...] = (
    (TIER_CORE, "core_10k.tsv"),
    (TIER_TIER2, "tier2_50k.tsv"),
    (TIER_TIER3, "tier3_final.tsv"),
    (TIER_TIER4, "tier4.tsv"),
)

#: How much of a provenance ``note`` the flat provenance repo keeps. Long enough for
#: every idempotence marker and for the first lines of a removal record; short enough
#: that one pathological entry cannot dominate the file.
NOTE_MAX_CHARS = 500

#: The four graded reading levels the release targets, beside the canonical ``neutral``.
GRADED_LEVELS: tuple[ReadingLevel, ...] = (
    ReadingLevel.GRADE_1,
    ReadingLevel.GRADE_5,
    ReadingLevel.GRADE_10,
    ReadingLevel.COLLEGE,
)

#: The four registers the release targets, beside the canonical ``plain``.
TARGET_REGISTERS: tuple[Register, ...] = (
    Register.INFORMAL,
    Register.FORMAL,
    Register.TECHNICAL,
    Register.MARKETING,
)

#: The two encyclopedia levels every tier was given (core also has grade 1 and grade 10).
ENCYCLOPEDIA_LEVELS: tuple[ReadingLevel, ...] = (ReadingLevel.GRADE_5, ReadingLevel.COLLEGE)

#: ``source`` value for an example sentence written by the per-sense examples stage.
SOURCE_PER_SENSE = "per_sense"
#: ``source`` value for an example that is a reading-level or register rewrite, or that
#: came from the original generation call.
SOURCE_RENDITIONS = "renditions"

#: ``relation`` value for the headword itself, in the ``inflections`` repo.
RELATION_LEMMA = "lemma"
#: ``relation`` value for a recorded derivation, in the ``inflections`` repo.
RELATION_DERIVATION = "derivation"
#: The single-valued :class:`~opengloss_generator.schema.Morphology` fields, in the order
#: the ``inflections`` repo reports them. Each attribute name doubles as its own
#: ``relation`` value, so there is exactly one place that pairs a field with its name.
_MORPHOLOGY_RELATIONS: tuple[str, ...] = (
    "plural",
    "past_tense",
    "past_participle",
    "present_participle",
    "third_person_singular",
    "comparative",
    "superlative",
)

#: The reconcile steps that remove an edge, with the provenance record header and the
#: per-edge line prefix each writes. Imported from the pass itself so the two cannot
#: drift (D-65, D-68).
_REMOVAL_STEPS: tuple[tuple[str, str, str], ...] = (
    ("tombstone", TOMBSTONE_RECORD_PREFIX, TOMBSTONE_LINE_PREFIX),
    ("dedup", DEDUP_RECORD_PREFIX, DEDUP_LINE_PREFIX),
    ("cap", CAP_RECORD_PREFIX, CAP_LINE_PREFIX),
)

#: Relation type values, longest first, so ``confusable_with`` is matched before a
#: hypothetical shorter suffix of it when an edge id is taken apart.
_RELATION_VALUES: tuple[str, ...] = tuple(
    sorted((member.value for member in RelationType), key=len, reverse=True)
)


def truncate_note(note: str | None, limit: int = NOTE_MAX_CHARS) -> str | None:
    """Return ``note`` shortened to ``limit`` characters, marked where it was cut.

    Args:
        note: The provenance note, possibly multi-line, possibly ``None``.
        limit: Maximum characters to keep.

    Returns:
        ``None`` unchanged; a short note unchanged; a long note cut to ``limit`` with a
        trailing ellipsis so a reader can tell truncation from content.
    """
    if note is None or len(note) <= limit:
        return note
    return f"{note[:limit]}…"


def relation_type_of_edge(edge: str) -> tuple[str | None, str | None, str | None]:
    """Take a derived edge id apart into ``(source sense id, type, target lexeme id)``.

    The id's shape is ``{source_sense_id}-{type}->{target_lexeme_id}`` (D-1). Splitting
    on the last ``->`` isolates the target; the type is then the known
    :class:`~opengloss_generator.schema.RelationType` value the remainder ends with,
    matched against the enum rather than by cutting at the last hyphen — a hyphenated
    headword (``well-being:noun:0-synonym->welfare``) makes the naive cut ambiguous.

    Args:
        edge: The edge id.

    Returns:
        The three parts, each ``None`` when the id does not have that shape.
    """
    head, separator, target = edge.rpartition("->")
    if not separator:
        return None, None, None
    for value in _RELATION_VALUES:
        suffix = f"-{value}"
        if head.endswith(suffix):
            return head[: -len(suffix)], value, target
    return None, None, target


def _iso(value: dt.datetime | None) -> str | None:
    """Return an ISO-8601 string for a datetime, or ``None``.

    Args:
        value: The timestamp.
    """
    return None if value is None else value.isoformat()


def _n_words(text: str) -> int:
    """Return the whitespace-delimited word count of ``text``.

    Args:
        text: The text to count.
    """
    return len(text.split())


def _grade(rendition: Rendition[Any]) -> float | None:
    """Return a rendition's measured readability grade, when one was recorded.

    Args:
        rendition: Any rendition.
    """
    return None if rendition.assessment is None else rendition.assessment.readability_grade


def _qa_flags(rendition: Rendition[Any]) -> list[str]:
    """Return a rendition's recorded quality flags as plain strings.

    Args:
        rendition: Any rendition.
    """
    if rendition.assessment is None:
        return []
    return [flag.value for flag in rendition.assessment.qa_flags]


# --------------------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------------------


class TierIndex:
    """Maps a lexeme id onto the frequency tier its headword was drawn from.

    The rank lists under ``data/core/`` are the record of which slice of the frequency
    ranking an entry belongs to, and therefore roughly which pipeline stages it received
    — earlier tiers received more; the coverage table in every card gives the exact
    per-field share rather than this class asserting one. Every exported row carries its
    tier so the difference is filterable rather than a footnote.
    """

    __slots__ = ("_tiers",)

    def __init__(self, tiers: dict[str, str] | None = None) -> None:
        """Build an index.

        Args:
            tiers: A ready-made ``lexeme_id -> tier`` mapping. Mostly for tests;
                :meth:`from_dir` is the normal constructor.
        """
        self._tiers: dict[str, str] = dict(tiers or {})

    @classmethod
    def from_dir(cls, directory: Path | None) -> TierIndex:
        """Read the rank lists from a directory.

        Args:
            directory: The directory holding ``core_10k.tsv``, ``tier2_50k.tsv``,
                ``tier3_final.tsv`` and ``tier4.tsv``. ``None`` or a missing directory
                yields an empty index. A missing individual file is *not* an error either
                (D-75): it is logged as a warning and skipped, so a chain that has not
                produced ``tier4.tsv`` yet does not crash an export — every entry that
                file would have named falls to a lower tier, or to :data:`TIER_UNKNOWN`
                when it is on none of the files present, which the export reports so the
                omission stays visible rather than silent.

        Returns:
            The index.
        """
        tiers: dict[str, str] = {}
        if directory is None or not directory.is_dir():
            return cls(tiers)
        for tier, filename in TIER_FILES:
            path = directory / filename
            if not path.is_file():
                _LOG.warning("tier_file_missing", path=str(path), tier=tier)
                continue
            for lexeme_id in _read_tsv_words(path):
                tiers.setdefault(lexeme_id, tier)
        return cls(tiers)

    def tier_of(self, lexeme_id: str) -> str:
        """Return the tier of an entry.

        Args:
            lexeme_id: The entry id.
        """
        return self._tiers.get(lexeme_id, TIER_UNKNOWN)

    def __len__(self) -> int:
        """Return how many headwords the index names."""
        return len(self._tiers)


def _read_tsv_words(path: Path) -> Iterator[str]:
    """Yield the slugified ``word`` column of a tab-separated rank list.

    Args:
        path: The TSV file. Its first line is a header naming the columns.
    """
    with path.open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        try:
            column = header.index("word")
        except ValueError:
            return
        for line in handle:
            cells = line.rstrip("\n").split("\t")
            if len(cells) > column and cells[column]:
                yield slugify(cells[column])


# --------------------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageFeature:
    """One row of the per-tier coverage table every card carries.

    Attributes:
        key: Stable identifier used as the :class:`Stats` counter key.
        label: How the card names the feature.
        grain: ``"lexeme"`` or ``"sense"`` — what the percentage is *of*.
        note: A short parenthetical the card appends, or the empty string.
    """

    key: str
    label: str
    grain: str
    note: str = ""


#: The coverage table, in card order. Chosen so a reader can see at a glance which
#: fields tier 3 does and does not have, which is the release's single biggest caveat.
COVERAGE_FEATURES: tuple[CoverageFeature, ...] = (
    CoverageFeature("gloss", "Canonical gloss", "sense"),
    CoverageFeature("domain", "Controlled domain tag", "sense"),
    CoverageFeature("gloss_levels", "Gloss at 4 reading levels", "sense"),
    CoverageFeature("gloss_registers", "Gloss in 4 registers", "sense"),
    CoverageFeature("examples", "At least one example", "sense"),
    CoverageFeature("example_levels", "Examples at 4 reading levels", "sense"),
    CoverageFeature("relations", "At least one relation", "sense"),
    CoverageFeature("queries", "Synthetic retrieval queries", "sense"),
    CoverageFeature("qa", "Grounded QA pairs", "sense"),
    CoverageFeature("etymology", "Etymology", "lexeme"),
    CoverageFeature("explanation", "Lexical explanation", "lexeme"),
    CoverageFeature("encyclopedia", "Encyclopedia (neutral)", "lexeme"),
    CoverageFeature(
        "encyclopedia_levels",
        "Encyclopedia at grade 5 + college",
        "lexeme",
        "core entries also carry grade 1 and grade 10",
    ),
    CoverageFeature("contrasts", "Contrast paragraphs", "lexeme"),
)


# --------------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Stats:
    """Everything the dataset cards report, counted from the rows actually written.

    Every field here is folded during the export pass or supplied by the derived
    exporters' own summaries; nothing is estimated and nothing is typed into a card by
    hand. ``rows``, ``shards`` and ``example_rows`` are filled in by the writer.
    """

    lexemes: int = 0
    live_senses: int = 0
    retired_senses: int = 0
    lexemes_by_tier: Counter[str] = field(default_factory=Counter)
    senses_by_tier: Counter[str] = field(default_factory=Counter)
    pos_histogram: Counter[str] = field(default_factory=Counter)
    kind_histogram: Counter[str] = field(default_factory=Counter)
    domain_root_histogram: Counter[str] = field(default_factory=Counter)
    domain_leaves: set[str] = field(default_factory=set)
    relation_type_histogram: Counter[str] = field(default_factory=Counter)
    relations_resolved: int = 0
    relations_total: int = 0
    tombstoned_relations: int = 0
    tombstoned_by_step: Counter[str] = field(default_factory=Counter)
    reading_levels: Counter[str] = field(default_factory=Counter)
    registers: Counter[str] = field(default_factory=Counter)
    gloss_renditions: int = 0
    example_renditions: int = 0
    examples_with_span: int = 0
    examples_by_source: Counter[str] = field(default_factory=Counter)
    encyclopedia_renditions: int = 0
    encyclopedia_words: int = 0
    explanation_renditions: int = 0
    etymologies: int = 0
    etymology_segments: int = 0
    inflection_forms: int = 0
    inflection_relations: Counter[str] = field(default_factory=Counter)
    queries: int = 0
    queries_headword_free: int = 0
    query_styles: Counter[str] = field(default_factory=Counter)
    qa_pairs: int = 0
    question_types: Counter[str] = field(default_factory=Counter)
    difficulties: Counter[str] = field(default_factory=Counter)
    contrasts: int = 0
    contrast_verdicts: Counter[str] = field(default_factory=Counter)
    provenance_records: int = 0
    provenance_cost_usd: float = 0.0
    provenance_models: Counter[str] = field(default_factory=Counter)
    provenance_stages: Counter[str] = field(default_factory=Counter)
    #: ``feature key -> tier -> owners that have it``.
    coverage: dict[str, Counter[str]] = field(default_factory=dict)
    #: ``"lexeme"``/``"sense"`` -> tier -> how many owners of that grain exist.
    coverage_totals: dict[str, Counter[str]] = field(default_factory=dict)
    #: ``(repo slug, config name) -> rows written``.
    rows: dict[tuple[str, str], int] = field(default_factory=dict)
    #: ``(repo slug, config name) -> (shard count, total bytes)``.
    shards: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    #: ``(repo slug, config name) -> the first row written``, for the card's example.
    example_rows: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: Summaries returned by the four free retrieval exporters, by repo slug.
    derived_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ``lexeme_id -> tier``, stamped by the store pass so a derived row's tier can never
    #: disagree with the tier its own entry's row carries.
    tier_lookup: dict[str, str] = field(default_factory=dict)

    def cover(self, feature: str, tier: str, *, present: bool) -> None:
        """Record whether one owner has one coverage feature.

        Args:
            feature: A :class:`CoverageFeature` key.
            tier: The owner's tier.
            present: Whether the owner has the feature.
        """
        counter = self.coverage.setdefault(feature, Counter())
        if present:
            counter[tier] += 1

    def count_owner(self, grain: str, tier: str) -> None:
        """Record one coverage denominator.

        Args:
            grain: ``"lexeme"`` or ``"sense"``.
            tier: The owner's tier.
        """
        self.coverage_totals.setdefault(grain, Counter())[tier] += 1

    def coverage_share(self, feature: str, grain: str, tier: str) -> float | None:
        """Return the share of owners in one tier that have one feature.

        Args:
            feature: A :class:`CoverageFeature` key.
            grain: ``"lexeme"`` or ``"sense"``.
            tier: The tier to report.

        Returns:
            A fraction in ``[0, 1]``, or ``None`` when that tier has no owners at all.
        """
        total = self.coverage_totals.get(grain, Counter()).get(tier, 0)
        if total == 0:
            return None
        return self.coverage.get(feature, Counter()).get(tier, 0) / total

    @property
    def tiers_present(self) -> tuple[str, ...]:
        """Return the tiers this export actually contains, in canonical order."""
        return tuple(tier for tier in TIERS if self.lexemes_by_tier.get(tier, 0) > 0)

    @property
    def total_rows(self) -> int:
        """Return how many rows the whole export wrote, across every repo."""
        return sum(self.rows.values())

    def rows_for(self, slug: str) -> int:
        """Return how many rows one repo wrote, across its configs.

        Args:
            slug: The repo slug.
        """
        return sum(count for (repo, _), count in self.rows.items() if repo == slug)

    def as_summary(self) -> dict[str, Any]:
        """Return a JSON-able view for the CLI's run summary."""
        return {
            "lexemes": self.lexemes,
            "live_senses": self.live_senses,
            "retired_senses_skipped": self.retired_senses,
            "lexemes_by_tier": dict(sorted(self.lexemes_by_tier.items())),
            "senses_by_tier": dict(sorted(self.senses_by_tier.items())),
            "relations": self.relations_total,
            "relations_resolved": self.relations_resolved,
            "tombstoned_relations": self.tombstoned_relations,
            "gloss_renditions": self.gloss_renditions,
            "example_renditions": self.example_renditions,
            "encyclopedia_renditions": self.encyclopedia_renditions,
            "queries": self.queries,
            "qa_pairs": self.qa_pairs,
            "contrasts": self.contrasts,
            "provenance_records": self.provenance_records,
            "provenance_cost_usd": round(self.provenance_cost_usd, 6),
            "rows": {f"{repo}/{config}": n for (repo, config), n in sorted(self.rows.items())},
            "shards": {
                f"{repo}/{config}": {"files": files, "bytes": size}
                for (repo, config), (files, size) in sorted(self.shards.items())
            },
        }


# --------------------------------------------------------------------------------------
# The builder
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _PendingContrast:
    """A contrast row whose far end has not been looked up yet."""

    row: dict[str, Any]
    target_sense_id: str | None
    target_lexeme_id: str | None


class RowBuilder:
    """Projects stored entries onto the release's store-derived repos.

    Usage is one pass: call :meth:`rows_for_entry` on every entry in ``lexeme_id`` order,
    then :meth:`finish` once to drain the contrast rows, which need the gloss index the
    pass built. :attr:`stats` is complete after :meth:`finish`.
    """

    __slots__ = ("_gloss_index", "_headwords", "_pending_contrasts", "_tiers", "stats")

    def __init__(self, tiers: TierIndex) -> None:
        """Build a row builder.

        Args:
            tiers: The tier index every row's ``tier`` column is read from.
        """
        self._tiers = tiers
        self.stats = Stats()
        self._gloss_index: dict[str, str] = {}
        self._headwords: dict[str, str] = {}
        self._pending_contrasts: list[_PendingContrast] = []

    def rows_for_entry(self, entry: Lexeme) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield every store-derived row one entry produces, and fold it into the stats.

        Args:
            entry: The entry to project. Never mutated.

        Yields:
            ``(repo slug, config name, row)`` triples, senses before entry-level rows.
            Contrast rows are *not* yielded here — they are buffered and drained by
            :meth:`finish`, because they carry the far end's headword and gloss.
        """
        tier = self._tiers.tier_of(entry.lexeme_id)
        stats = self.stats
        stats.lexemes += 1
        stats.lexemes_by_tier[tier] += 1
        stats.kind_histogram[entry.kind.value] += 1
        stats.count_owner("lexeme", tier)
        stats.tier_lookup[entry.lexeme_id] = tier
        self._headwords[entry.lexeme_id] = entry.headword

        all_senses = entry.iter_senses()
        live = [item for item in all_senses if not item[1].retired]
        stats.retired_senses += len(all_senses) - len(live)

        sense_ids: list[str] = []
        for pos_entry, sense, sense_id in live:
            sense_ids.append(sense_id)
            self._gloss_index[sense_id] = sense.canonical_gloss()
            yield from self._sense_rows(entry, pos_entry, sense, sense_id, tier)

        yield "lexicon", "default", self._lexicon_row(entry, tier, sense_ids, len(all_senses))
        yield from self._prose_rows(entry, tier)
        yield from self._etymology_rows(entry, tier)
        yield from self._inflection_rows(entry, tier)
        yield from self._provenance_rows(entry, tier)
        yield from self._tombstoned_rows(entry, tier)
        self._buffer_contrasts(entry, tier)

    def finish(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield the buffered contrast rows, with the far end's headword and gloss filled.

        Yields:
            ``("contrasts", "default", row)`` triples in the order the contrasts were
            met, which is ``(lexeme_id, edge_id)`` order because the pass visits entries
            in id order.
        """
        for pending in self._pending_contrasts:
            row = pending.row
            target_sense = pending.target_sense_id
            target_lexeme = pending.target_lexeme_id
            row["target_gloss"] = (
                self._gloss_index.get(target_sense) if target_sense is not None else None
            )
            row["target_headword"] = (
                self._headwords.get(target_lexeme) if target_lexeme is not None else None
            )
            yield "contrasts", "default", row
        self._pending_contrasts.clear()

    # -- sense-grained rows ------------------------------------------------------------

    def _sense_rows(
        self,
        entry: Lexeme,
        pos_entry: POSEntry,
        sense: Sense,
        sense_id: str,
        tier: str,
    ) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield the nested sense row and every flat row that sense produces."""
        stats = self.stats
        pos = pos_entry.pos.value
        domain = sense.domain.value if sense.domain is not None else None
        forms = pos_entry.morphology.inflected_forms()

        stats.live_senses += 1
        stats.senses_by_tier[tier] += 1
        stats.pos_histogram[pos] += 1
        stats.count_owner("sense", tier)
        if sense.domain is not None:
            stats.domain_root_histogram[root_of(sense.domain)] += 1
            stats.domain_leaves.add(sense.domain.value)

        keys = {"sense_id": sense_id, "lexeme_id": entry.lexeme_id, "headword": entry.headword}
        flat_keys = {**keys, "pos": pos, "sense_index": sense.index, "domain": domain, "tier": tier}

        gloss_rows = self._gloss_rows(sense, flat_keys)
        example_rows = self._example_rows(entry, sense, flat_keys)
        relation_rows = self._relation_rows(entry, sense, sense_id, pos, tier)
        query_rows = self._query_rows(entry, sense, sense_id, forms, flat_keys)
        qa_rows = self._qa_rows(sense, sense_id, flat_keys)

        yield (
            "senses",
            "default",
            {
                **flat_keys,
                "upos": upos_for(entry, pos_entry.pos),
                "domain_root": root_of(sense.domain) if sense.domain is not None else None,
                "secondary_domains": [tag.value for tag in sense.secondary_domains],
                "gloss": sense.canonical_gloss(),
                "gloss_renditions": [_strip(row, _PROSE_KEYS) for row in gloss_rows],
                "examples": [_strip(row, _EXAMPLE_KEYS) for row in example_rows],
                "relations": [_strip(row, _RELATION_KEYS) for row in relation_rows],
                "queries": [_strip(row, _QUERY_KEYS) for row in query_rows],
                "qa_pairs": [_strip(row, _QA_KEYS) for row in qa_rows],
                "n_examples": len(example_rows),
                "n_relations": len(relation_rows),
                "n_queries": len(query_rows),
                "n_qa_pairs": len(qa_rows),
                "qa_score": None if sense.assessment is None else sense.assessment.qa_score,
            },
        )

        for row in gloss_rows:
            yield "definitions", "default", row
        for row in example_rows:
            yield "examples", "default", row
        for row in relation_rows:
            yield "relations", "relations", row
        for row in query_rows:
            yield "queries", "default", row
        for row in qa_rows:
            yield "qa-pairs", "default", row

        self._cover_sense(
            sense,
            tier,
            example_rows=example_rows,
            relation_rows=relation_rows,
            query_rows=query_rows,
            qa_rows=qa_rows,
        )

    def _gloss_rows(self, sense: Sense, flat_keys: dict[str, Any]) -> list[dict[str, Any]]:
        """Return one flat definition row per stored gloss rendition."""
        stats = self.stats
        rows: list[dict[str, Any]] = []
        for rendition in sense.gloss:
            stats.gloss_renditions += 1
            stats.reading_levels[rendition.reading_level.value] += 1
            stats.registers[rendition.style.value] += 1
            rows.append(
                {
                    **flat_keys,
                    "reading_level": rendition.reading_level.value,
                    "register": rendition.style.value,
                    "text": rendition.content,
                    "readability_grade": _grade(rendition),
                    "is_canonical": rendition.is_canonical,
                    "qa_flags": _qa_flags(rendition),
                }
            )
        return rows

    def _example_rows(
        self, entry: Lexeme, sense: Sense, flat_keys: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return one flat example row per stored example rendition."""
        stats = self.stats
        rows: list[dict[str, Any]] = []
        for rendition in sense.examples:
            example: Example = rendition.content
            span = example.span
            source = self._example_source(entry, rendition.provenance_id)
            stats.example_renditions += 1
            stats.examples_by_source[source] += 1
            if span is not None:
                stats.examples_with_span += 1
            rows.append(
                {
                    **flat_keys,
                    "reading_level": rendition.reading_level.value,
                    "register": rendition.style.value,
                    "text": example.text,
                    "span_start": None if span is None else span[0],
                    "span_end": None if span is None else span[1],
                    "readability_grade": _grade(rendition),
                    "source": source,
                }
            )
        return rows

    @staticmethod
    def _example_source(entry: Lexeme, provenance_id: str | None) -> str:
        """Return whether an example came from the per-sense examples stage.

        Args:
            entry: The owning entry, whose provenance table resolves the id.
            provenance_id: The rendition's provenance pointer, possibly ``None``.

        Returns:
            :data:`SOURCE_PER_SENSE` when the record that wrote it is the examples stage
            (D-53's verified sense-disambiguated sentences), otherwise
            :data:`SOURCE_RENDITIONS` — which covers both a reading-level/register rewrite
            and an example written by the original generation call.
        """
        if provenance_id is None:
            return SOURCE_RENDITIONS
        record = entry.provenance.get(provenance_id)
        if record is not None and record.stage is StageName.EXAMPLES:
            return SOURCE_PER_SENSE
        return SOURCE_RENDITIONS

    def _relation_rows(
        self, entry: Lexeme, sense: Sense, sense_id: str, pos: str, tier: str
    ) -> list[dict[str, Any]]:
        """Return one flat relation row per live typed edge on this sense."""
        stats = self.stats
        rows: list[dict[str, Any]] = []
        for relation in sense.relations:
            target = relation.target
            resolved = target.sense_id is not None
            stats.relations_total += 1
            stats.relation_type_histogram[relation.type.value] += 1
            stats.relations_resolved += int(resolved)
            rows.append(
                {
                    "edge_id": make_edge_id(sense_id, relation.type.value, target.lexeme_id),
                    "source_sense_id": sense_id,
                    "source_lexeme_id": entry.lexeme_id,
                    "headword": entry.headword,
                    "pos": pos,
                    "type": relation.type.value,
                    "target_term": target.term,
                    "target_lexeme_id": target.lexeme_id,
                    "target_sense_id": target.sense_id,
                    "resolved": resolved,
                    "confidence": target.confidence,
                    "note": relation.note,
                    "tier": tier,
                }
            )
        return rows

    def _query_rows(
        self,
        entry: Lexeme,
        sense: Sense,
        sense_id: str,
        forms: list[str],
        flat_keys: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return one flat query row per stored synthetic query."""
        stats = self.stats
        rows: list[dict[str, Any]] = []
        for query_id, query in zip(sense.query_ids(sense_id), sense.queries, strict=True):
            headword_free = spans.find_span(query.text, entry.headword, forms) is None
            stats.queries += 1
            stats.query_styles[query.style.value] += 1
            stats.queries_headword_free += int(headword_free)
            rows.append(
                {
                    "query_id": query_id,
                    **flat_keys,
                    "style": query.style.value,
                    "text": query.text,
                    "headword_free": headword_free,
                }
            )
        return rows

    def _qa_rows(
        self, sense: Sense, sense_id: str, flat_keys: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return one flat QA row per stored grounded question/answer pair."""
        stats = self.stats
        rows: list[dict[str, Any]] = []
        for qa_id, pair in zip(sense.qa_ids(sense_id), sense.qa, strict=True):
            stats.qa_pairs += 1
            stats.question_types[pair.question_type.value] += 1
            stats.difficulties[pair.difficulty.value] += 1
            rows.append(
                {
                    "qa_id": qa_id,
                    **flat_keys,
                    "question": pair.question,
                    "answer": pair.answer,
                    "question_type": pair.question_type.value,
                    "difficulty": pair.difficulty.value,
                    "grounded_in": list(pair.grounded_in),
                }
            )
        return rows

    def _cover_sense(
        self,
        sense: Sense,
        tier: str,
        *,
        example_rows: list[dict[str, Any]],
        relation_rows: list[dict[str, Any]],
        query_rows: list[dict[str, Any]],
        qa_rows: list[dict[str, Any]],
    ) -> None:
        """Fold one live sense into the per-tier coverage counters."""
        stats = self.stats
        stats.cover("gloss", tier, present=True)
        stats.cover("domain", tier, present=sense.domain is not None)
        stats.cover(
            "gloss_levels",
            tier,
            present=all(sense.gloss.has(level, Register.PLAIN) for level in GRADED_LEVELS),
        )
        stats.cover(
            "gloss_registers",
            tier,
            present=all(
                sense.gloss.has(ReadingLevel.NEUTRAL, register) for register in TARGET_REGISTERS
            ),
        )
        stats.cover("examples", tier, present=bool(example_rows))
        example_levels = {row["reading_level"] for row in example_rows}
        stats.cover(
            "example_levels",
            tier,
            present=all(level.value in example_levels for level in GRADED_LEVELS),
        )
        stats.cover("relations", tier, present=bool(relation_rows))
        stats.cover("queries", tier, present=bool(query_rows))
        stats.cover("qa", tier, present=bool(qa_rows))

    # -- entry-grained rows ------------------------------------------------------------

    def _lexicon_row(
        self, entry: Lexeme, tier: str, sense_ids: list[str], total_senses: int
    ) -> dict[str, Any]:
        """Return the nested ``lexicon`` row for one entry."""
        models = sorted({record.model for record in entry.provenance.values()})
        cost = sum(record.cost_usd for record in entry.provenance.values())
        return {
            "lexeme_id": entry.lexeme_id,
            "headword": entry.headword,
            "language": entry.language,
            "kind": entry.kind.value,
            "status": entry.status.value,
            "tier": tier,
            "pos_list": [pos_entry.pos.value for pos_entry in entry.pos_entries],
            "sense_ids": sense_ids,
            "n_live_senses": len(sense_ids),
            "n_retired_senses": total_senses - len(sense_ids),
            "is_stopword": entry.is_stopword,
            "frequency": entry.frequency,
            "zipf": entry.zipf,
            "morphology": [_morphology_struct(pos_entry) for pos_entry in entry.pos_entries],
            "etymology": _etymology_struct(entry),
            "lexical_explanation": [
                _prose_struct(rendition) for rendition in entry.lexical_explanation
            ],
            "encyclopedia": [_prose_struct(rendition) for rendition in entry.encyclopedia],
            "contrasts": [
                {
                    "edge_id": contrast.edge_id,
                    "target_sense_id": contrast.target_sense_id,
                    "verdict": contrast.verdict.value,
                    "text": contrast.canonical_text(),
                }
                for contrast in entry.contrasts
            ],
            "provenance_summary": {
                "models": models,
                "total_cost_usd": cost,
                "n_records": len(entry.provenance),
            },
            "created_at": _iso(entry.created_at),
            "updated_at": _iso(entry.updated_at),
        }

    def _prose_rows(self, entry: Lexeme, tier: str) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield flat encyclopedia and lexical-explanation rows, and fold their coverage."""
        stats = self.stats
        keys = {"lexeme_id": entry.lexeme_id, "headword": entry.headword, "tier": tier}
        for config, renditions in (
            ("encyclopedia", entry.encyclopedia),
            ("explanation", entry.lexical_explanation),
        ):
            for rendition in renditions:
                words = _n_words(rendition.content)
                if config == "encyclopedia":
                    stats.encyclopedia_renditions += 1
                    stats.encyclopedia_words += words
                else:
                    stats.explanation_renditions += 1
                yield (
                    "encyclopedia",
                    config,
                    {
                        **keys,
                        "kind": entry.kind.value,
                        "reading_level": rendition.reading_level.value,
                        "register": rendition.style.value,
                        "text": rendition.content,
                        "readability_grade": _grade(rendition),
                        "n_words": words,
                        "is_canonical": rendition.is_canonical,
                    },
                )

        stats.cover("explanation", tier, present=bool(entry.lexical_explanation))
        stats.cover("encyclopedia", tier, present=entry.encyclopedia.canonical() is not None)
        stats.cover(
            "encyclopedia_levels",
            tier,
            present=all(
                entry.encyclopedia.has(level, Register.PLAIN) for level in ENCYCLOPEDIA_LEVELS
            ),
        )
        stats.cover("contrasts", tier, present=bool(entry.contrasts))

    def _etymology_rows(
        self, entry: Lexeme, tier: str
    ) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield the flat etymology row, when the entry has an etymology."""
        etymology = entry.etymology
        self.stats.cover("etymology", tier, present=etymology is not None)
        if etymology is None:
            return
        self.stats.etymologies += 1
        self.stats.etymology_segments += len(etymology.segments)
        yield (
            "etymology",
            "default",
            {
                "lexeme_id": entry.lexeme_id,
                "headword": entry.headword,
                "tier": tier,
                "kind": entry.kind.value,
                "summary": etymology.summary,
                "segments": [_segment_struct(segment) for segment in etymology.segments],
                "cognates": list(etymology.cognates),
                "references": list(etymology.references),
                "n_segments": len(etymology.segments),
            },
        )

    def _inflection_rows(
        self, entry: Lexeme, tier: str
    ) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield the flat form→lemma rows for every POS entry's morphology (D-75).

        One row per non-empty inflected field, one per recorded derivation, and one
        ``lemma`` row for the headword itself — per POS entry, since a homograph like
        "record" (noun and verb) resolves the same headword to two different parts of
        speech. Emitted regardless of sense liveness or entry status, matching how the
        nested ``lexicon`` row's own ``morphology`` list is built from every POS entry.
        """
        stats = self.stats
        for pos_entry in entry.pos_entries:
            morphology = pos_entry.morphology
            keys = {
                "lexeme_id": entry.lexeme_id,
                "headword": entry.headword,
                "pos": pos_entry.pos.value,
                "tier": tier,
            }
            for form, relation in self._forms_of(entry.headword, morphology):
                stats.inflection_forms += 1
                stats.inflection_relations[relation] += 1
                yield (
                    "inflections",
                    "default",
                    {
                        "form": form,
                        "form_normalized": form.lower(),
                        **keys,
                        "relation": relation,
                    },
                )

    @staticmethod
    def _forms_of(headword: str, morphology: Any) -> Iterator[tuple[str, str]]:  # noqa: ANN401
        """Yield ``(form, relation)`` pairs for one POS entry's morphology.

        Args:
            headword: The owning entry's headword, emitted once as the ``lemma`` row.
            morphology: The POS entry's :class:`~opengloss_generator.schema.Morphology`.
        """
        yield headword, RELATION_LEMMA
        for relation in _MORPHOLOGY_RELATIONS:
            form = getattr(morphology, relation)
            if form:
                yield form, relation
        for derivation in morphology.derivations:
            if derivation:
                yield derivation, RELATION_DERIVATION

    def _provenance_rows(
        self, entry: Lexeme, tier: str
    ) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield one flat row per provenance record, in ``p<n>`` numeric order."""
        stats = self.stats
        for provenance_id, record in _provenance_in_key_order(entry):
            stats.provenance_records += 1
            stats.provenance_cost_usd += record.cost_usd
            stats.provenance_models[record.model] += 1
            stats.provenance_stages[record.stage.value] += 1
            yield (
                "provenance",
                "default",
                {
                    "lexeme_id": entry.lexeme_id,
                    "headword": entry.headword,
                    "tier": tier,
                    "provenance_id": provenance_id,
                    "stage": record.stage.value,
                    "model": record.model,
                    "provider": record.provider,
                    "prompt_version": record.prompt_version,
                    "service_tier": record.service_tier,
                    "input_tokens": record.input_tokens,
                    "cached_input_tokens": record.cached_input_tokens,
                    "output_tokens": record.output_tokens,
                    "cost_usd": record.cost_usd,
                    "attempts": record.attempts,
                    "run_id": record.run_id,
                    "note": truncate_note(record.note),
                    "generated_at": _iso(record.generated_at),
                },
            )

    def _tombstoned_rows(
        self, entry: Lexeme, tier: str
    ) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield one row per relation edge the free reconcile pass removed (D-65, D-68)."""
        stats = self.stats
        for provenance_id, record in _provenance_in_key_order(entry):
            note = record.note
            if note is None or not note.startswith("reconcile:"):
                continue
            for row in _parse_removal_record(note):
                stats.tombstoned_relations += 1
                stats.tombstoned_by_step[row["step"]] += 1
                yield (
                    "relations",
                    "tombstoned",
                    {
                        **row,
                        "source_lexeme_id": entry.lexeme_id,
                        "headword": entry.headword,
                        "provenance_id": provenance_id,
                        "tier": tier,
                    },
                )

    def _buffer_contrasts(self, entry: Lexeme, tier: str) -> None:
        """Buffer one row per contrast rendition, for :meth:`finish` to complete."""
        stats = self.stats
        for contrast in entry.contrasts:
            source_sense_id, relation_type, target_lexeme_id = relation_type_of_edge(
                contrast.edge_id
            )
            stats.contrasts += 1
            stats.contrast_verdicts[contrast.verdict.value] += 1
            for rendition in contrast.text:
                self._pending_contrasts.append(
                    _PendingContrast(
                        row={
                            "edge_id": contrast.edge_id,
                            "source_sense_id": source_sense_id,
                            "source_lexeme_id": entry.lexeme_id,
                            "source_headword": entry.headword,
                            "source_gloss": self._gloss_index.get(source_sense_id or ""),
                            "target_sense_id": contrast.target_sense_id,
                            "target_lexeme_id": target_lexeme_id,
                            "target_headword": None,
                            "target_gloss": None,
                            "relation_type": relation_type,
                            "verdict": contrast.verdict.value,
                            "reading_level": rendition.reading_level.value,
                            "register": rendition.style.value,
                            "text": rendition.content,
                            "tier": tier,
                        },
                        target_sense_id=contrast.target_sense_id,
                        target_lexeme_id=target_lexeme_id,
                    )
                )


# --------------------------------------------------------------------------------------
# Struct helpers
# --------------------------------------------------------------------------------------

#: Which keys of a flat definition row survive into the nested ``gloss_renditions`` struct.
_PROSE_KEYS = ("reading_level", "register", "text", "readability_grade")
_EXAMPLE_KEYS = (
    "text",
    "span_start",
    "span_end",
    "reading_level",
    "register",
    "readability_grade",
    "source",
)
_RELATION_KEYS = (
    "type",
    "target_term",
    "target_lexeme_id",
    "target_sense_id",
    "confidence",
    "note",
)
_QUERY_KEYS = ("style", "text", "headword_free")
_QA_KEYS = ("question", "answer", "question_type", "difficulty", "grounded_in")


def _strip(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return the subset of a flat row that a nested struct keeps.

    The nested repos and the flat repos are two views of the same values, so the flat row
    is built once and the struct is a projection of it — which is also what guarantees
    the two can never disagree about a field.

    Args:
        row: A flat row.
        keys: The struct's member names.
    """
    return {key: row[key] for key in keys}


def _prose_struct(rendition: Rendition[str]) -> dict[str, Any]:
    """Return the ``list<struct>`` member for one prose rendition.

    Args:
        rendition: A gloss, encyclopedia or explanation rendition.
    """
    return {
        "reading_level": rendition.reading_level.value,
        "register": rendition.style.value,
        "text": rendition.content,
        "readability_grade": _grade(rendition),
    }


def _segment_struct(segment: Any) -> dict[str, Any]:  # noqa: ANN401 - EtymologySegment
    """Return the struct for one etymology segment.

    Args:
        segment: An :class:`~opengloss_generator.schema.EtymologySegment`.
    """
    return {
        "language": segment.language,
        "language_code": segment.language_code,
        "form": segment.form,
        "meaning": segment.meaning,
        "era": segment.era,
    }


def _morphology_struct(pos_entry: POSEntry) -> dict[str, Any]:
    """Return the struct for one POS entry's morphology and collocations.

    Args:
        pos_entry: The POS entry.
    """
    morphology = pos_entry.morphology
    return {
        "pos": pos_entry.pos.value,
        "plural": morphology.plural,
        "past_tense": morphology.past_tense,
        "past_participle": morphology.past_participle,
        "present_participle": morphology.present_participle,
        "third_person_singular": morphology.third_person_singular,
        "comparative": morphology.comparative,
        "superlative": morphology.superlative,
        "derivations": list(morphology.derivations),
        "collocations": list(pos_entry.collocations),
    }


def _etymology_struct(entry: Lexeme) -> dict[str, Any] | None:
    """Return the nested etymology struct for an entry, or ``None``.

    Args:
        entry: The entry.
    """
    etymology = entry.etymology
    if etymology is None:
        return None
    return {
        "summary": etymology.summary,
        "segments": [_segment_struct(segment) for segment in etymology.segments],
        "cognates": list(etymology.cognates),
        "references": list(etymology.references),
    }


def _provenance_in_key_order(entry: Lexeme) -> list[tuple[str, Any]]:
    """Return an entry's provenance table ordered by the integer in its ``p<n>`` key.

    The store serialises with sorted keys, so the table reads back ``p1, p10, p100, p11``
    — lexicographic, not chronological. Ordering numerically here makes the exported rows
    match the order the records were handed out, which is the order a reader expects.

    Args:
        entry: The entry.
    """

    def sort_key(item: tuple[str, Any]) -> tuple[int, str]:
        key = item[0]
        digits = key[1:]
        return (int(digits) if digits.isdigit() else 0, key)

    return sorted(entry.provenance.items(), key=sort_key)


def _parse_removal_record(note: str) -> Iterator[dict[str, Any]]:
    """Yield one removed-edge row per line of a reconcile removal record.

    The record's shape is a header naming the sense followed by one line per edge,
    ``<prefix><type> -> <term> [<note>]`` (D-65). Nothing here guesses: the prefixes are
    imported from the pass that writes them, and a line that does not parse is skipped
    rather than turned into a half-filled row.

    Args:
        note: The provenance record's ``note``.

    Yields:
        Partial rows carrying ``edge_id``, ``source_sense_id``, ``type``,
        ``target_term``, ``target_lexeme_id``, ``step`` and ``reason``. The caller adds
        the entry-level columns.
    """
    lines = note.splitlines()
    if not lines:
        return
    header = lines[0]
    for step, record_prefix, line_prefix in _REMOVAL_STEPS:
        if not header.startswith(record_prefix) or header.startswith(line_prefix):
            continue
        sense_id = header[len(record_prefix) :].strip()
        if not sense_id:
            return
        for line in lines[1:]:
            if not line.startswith(line_prefix):
                continue
            parsed = _parse_removal_line(line[len(line_prefix) :])
            if parsed is None:
                continue
            relation_type, term, reason = parsed
            yield {
                "edge_id": make_edge_id(sense_id, relation_type, slugify(term)),
                "source_sense_id": sense_id,
                "type": relation_type,
                "target_term": term,
                "target_lexeme_id": slugify(term),
                "step": step,
                "reason": reason,
            }
        return


def _parse_removal_line(body: str) -> tuple[str, str, str] | None:
    """Take one removal line's body apart into ``(type, target term, reason)``.

    Args:
        body: The line with its step prefix already removed, e.g.
            ``synonym -> rappel [demoted: nano invalid]``.

    Returns:
        The three parts, or ``None`` when the line does not have that shape.
    """
    relation_type, separator, rest = body.partition(" -> ")
    if not separator or relation_type not in _RELATION_VALUES:
        return None
    term, bracket, reason = rest.rpartition(" [")
    if not bracket or not reason.endswith("]"):
        return None
    return relation_type, term.strip(), reason[:-1]
