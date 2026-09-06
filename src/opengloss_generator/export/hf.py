"""``export-hf``: build the whole v2.0 Hugging Face release family from one store.

One command writes one local directory per dataset repo — parquet shards plus a finished
``README.md`` — for the sixteen repos registered in
:mod:`opengloss_generator.export.hf_schemas`. Nothing here talks to the network unless
``push_repos`` is called explicitly; the export itself is offline and free, like every
other module in this package.

How it is put together
----------------------

* **One streaming pass over the store** builds every row of the twelve store-derived
  repos and, at the same time, every statistic the cards print
  (:class:`~opengloss_generator.export.hf_rows.RowBuilder`). The pass runs even when
  ``--repos`` selects only a derived repo, because a card that says "54,724 lexemes"
  must have counted them.
* **The four derived repos** (``retrieval-pairs``, ``retrieval-triples``, ``qrels``,
  ``pretrain``) are produced by calling the existing free exporters' own functions and
  reshaping their records into parquet. They are not reimplemented here: a second
  implementation of "which negative is hard" would be a second thing to keep true.
* **Every column has an explicit ``pyarrow`` type.** Rows are projected onto the config's
  column list before writing, so a typo in a row builder raises rather than quietly
  writing a null column, and nothing is inferred from whichever row happened to be first.
* **Shards roll on rows and on bytes**, so no file exceeds roughly 300 MB whatever the
  row size, and a config with no rows still gets one empty, correctly-typed shard — a
  consumer's ``load_dataset`` must not fail just because a stage never ran.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

from opengloss_generator.export.hf_cards import render_card
from opengloss_generator.export.hf_rows import RowBuilder, Stats, TierIndex
from opengloss_generator.export.hf_schemas import (
    DEFAULT_OWNER,
    DEFAULT_RELEASE,
    REPOS_BY_SLUG,
    ConfigSpec,
    RepoSpec,
    resolve_repos,
)
from opengloss_generator.export.pairs import export_pairs
from opengloss_generator.export.pretrain import TEMPLATES as PRETRAIN_TEMPLATES
from opengloss_generator.export.pretrain import export_pretrain
from opengloss_generator.export.qrels import build_qrels
from opengloss_generator.export.triples import build_triples
from opengloss_generator.identity import slugify
from opengloss_generator.schema import ReadingLevel

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from opengloss_generator.store import LexemeStore

__all__ = [
    "DEFAULT_MAX_SHARD_BYTES",
    "DEFAULT_SHARD_ROWS",
    "HfExportResult",
    "export_hf",
    "push_repos",
]

#: Rows per shard before rolling over. Deliberately generous: the byte ceiling below is
#: what actually bounds a shard for the wide, prose-heavy repos, and this only bounds the
#: narrow ones so no single file becomes awkward to stream.
DEFAULT_SHARD_ROWS = 500_000

#: Byte ceiling per shard, checked after every batch. ~300 MB, the size above which the
#: Hub's own tooling starts recommending a split.
DEFAULT_MAX_SHARD_BYTES = 300_000_000

#: How many rows are accumulated before one arrow batch is written and the file's size is
#: re-checked. Small enough that the byte ceiling is respected closely, large enough that
#: per-batch overhead does not dominate.
_BATCH_ROWS = 2_048

#: How many colon-separated parts a sense id has (``lexeme:pos:index``); a shorter id in
#: the document namespace is a ``{lexeme_id}:encyclopedia`` id instead.
_SENSE_ID_PARTS = 3

#: A span is a ``[start, end]`` pair.
_SPAN_PARTS = 2

#: Reading levels the pretraining corpus is rendered at by default.
DEFAULT_PRETRAIN_LEVELS: tuple[ReadingLevel, ...] = (
    ReadingLevel.NEUTRAL,
    ReadingLevel.GRADE_5,
    ReadingLevel.COLLEGE,
)


# --------------------------------------------------------------------------------------
# Shard writing
# --------------------------------------------------------------------------------------


class _ShardWriter:
    """Writes one config's rows as a series of size-bounded parquet shards.

    Rows arrive one dict at a time, are projected onto the config's exact column list,
    and are written in batches. A shard is closed when it passes either the row cap or
    the byte cap; the byte check happens after a batch is flushed, so it reads the real
    on-disk size rather than an estimate. Both caps are therefore honoured at *batch*
    granularity — a shard can overshoot the byte ceiling by at most one batch, which is
    why the ceiling is set below the size that actually matters rather than at it.
    """

    __slots__ = (
        "_batch",
        "_batch_rows",
        "_columns",
        "_dir",
        "_index",
        "_max_bytes",
        "_max_rows",
        "_path",
        "_rows_in_shard",
        "_schema",
        "_writer",
        "bytes_written",
        "first_row",
        "rows",
        "shards",
    )

    def __init__(self, out_dir: Path, schema: pa.Schema, *, max_rows: int, max_bytes: int) -> None:
        """Open a writer.

        Args:
            out_dir: Directory the shards are written into; created if absent.
            schema: The config's explicit schema.
            max_rows: Rows per shard before rolling over.
            max_bytes: Approximate byte ceiling per shard.
        """
        self._dir = out_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._schema = schema
        self._columns = tuple(schema.names)
        self._max_rows = max(1, max_rows)
        self._max_bytes = max(1, max_bytes)
        self._batch: list[dict[str, Any]] = []
        # Never buffer past the row cap: a caller that asks for one-row shards must get
        # them, and the byte check only runs when a batch is flushed.
        self._batch_rows = min(_BATCH_ROWS, self._max_rows)
        self._writer: pq.ParquetWriter | None = None
        self._path: Path | None = None
        self._index = 0
        self._rows_in_shard = 0
        self.rows = 0
        self.shards = 0
        self.bytes_written = 0
        self.first_row: dict[str, Any] | None = None

    def write(self, row: dict[str, Any]) -> None:
        """Buffer one row.

        Args:
            row: A row whose keys are a subset of the config's columns.

        Raises:
            ValueError: When the row carries a column the schema does not have. A silent
                drop would hide a row-builder bug behind a correctly-typed file.
        """
        extra = set(row) - set(self._columns)
        if extra:
            raise ValueError(f"row has columns not in the schema: {sorted(extra)}")
        if self.first_row is None:
            self.first_row = dict(row)
        self._batch.append({name: row.get(name) for name in self._columns})
        self.rows += 1
        if len(self._batch) >= self._batch_rows:
            self._flush()

    def close(self) -> tuple[int, int]:
        """Flush and close, writing an empty shard when nothing was ever written.

        Returns:
            ``(shard count, total bytes)``.
        """
        self._flush()
        self._close_shard()
        if self.shards == 0:
            self._open_shard()
            self._close_shard()
        return self.shards, self.bytes_written

    # -- internals ---------------------------------------------------------------------

    def _flush(self) -> None:
        """Write the buffered rows as one arrow batch, rolling the shard if it is full."""
        if not self._batch:
            return
        writer = self._writer if self._writer is not None else self._open_shard()
        table = pa.Table.from_pylist(self._batch, schema=self._schema)
        writer.write_table(table)
        self._rows_in_shard += len(self._batch)
        self._batch.clear()
        if self._rows_in_shard >= self._max_rows or self._shard_size() >= self._max_bytes:
            self._close_shard()

    def _open_shard(self) -> pq.ParquetWriter:
        """Start a new shard file and return its writer."""
        self._path = self._dir / f"train-{self._index:05d}.parquet"
        self._writer = pq.ParquetWriter(self._path, self._schema, compression="zstd")
        self._rows_in_shard = 0
        return self._writer

    def _close_shard(self) -> None:
        """Close the open shard and record its size."""
        if self._writer is None or self._path is None:
            return
        self._writer.close()
        self._writer = None
        self.bytes_written += self._path.stat().st_size
        self.shards += 1
        self._index += 1
        self._path = None

    def _shard_size(self) -> int:
        """Return the open shard's current on-disk size, or 0 when it is not there yet."""
        if self._path is None or not self._path.exists():
            return 0
        return self._path.stat().st_size


class _WriterSet:
    """Holds one :class:`_ShardWriter` per selected ``(repo, config)`` and routes rows.

    A row addressed to a config the caller did not select is dropped silently — that is
    what ``--repos`` means — while the statistics for it are still folded, because the
    cards report on the release, not on the subset that happened to be written.
    """

    __slots__ = ("_max_bytes", "_max_rows", "_out_dir", "_writers")

    def __init__(
        self,
        out_dir: Path,
        specs: Sequence[RepoSpec],
        *,
        max_rows: int,
        max_bytes: int,
        release: str = DEFAULT_RELEASE,
    ) -> None:
        """Open a writer for every config of every selected repo.

        Args:
            out_dir: Root directory the repo directories are created under.
            specs: The selected repos.
            max_rows: Rows per shard.
            max_bytes: Byte ceiling per shard.
            release: The release label the repo directories are named for.
        """
        self._out_dir = out_dir
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._writers: dict[tuple[str, str], _ShardWriter] = {}
        for spec in specs:
            for config in spec.configs:
                self._writers[spec.slug, config.name] = _ShardWriter(
                    data_dir(out_dir, spec, config, release),
                    config.schema,
                    max_rows=max_rows,
                    max_bytes=max_bytes,
                )

    def write(self, slug: str, config: str, row: dict[str, Any]) -> None:
        """Route one row to its config's writer, if that config was selected.

        Args:
            slug: The repo slug.
            config: The config name.
            row: The row.
        """
        writer = self._writers.get((slug, config))
        if writer is not None:
            writer.write(row)

    def wants(self, slug: str) -> bool:
        """Return whether any config of a repo was selected.

        Args:
            slug: The repo slug.
        """
        return any(key[0] == slug for key in self._writers)

    def close(self, stats: Stats) -> None:
        """Close every writer and fold its counts into ``stats``.

        Args:
            stats: The statistics object to record row counts, shard counts and the
                per-config example row into.
        """
        for key, writer in self._writers.items():
            shards, size = writer.close()
            stats.rows[key] = writer.rows
            stats.shards[key] = (shards, size)
            if writer.first_row is not None:
                stats.example_rows[key] = writer.first_row


def repo_dir(out_dir: Path, spec: RepoSpec, release: str = DEFAULT_RELEASE) -> Path:
    """Return the local directory one repo is written to.

    Args:
        out_dir: The export root.
        spec: The repo.
        release: The release label the directory is named for.
    """
    return out_dir / spec.name(release)


def data_dir(
    out_dir: Path, spec: RepoSpec, config: ConfigSpec, release: str = DEFAULT_RELEASE
) -> Path:
    """Return the directory one config's shards are written to.

    Args:
        out_dir: The export root.
        spec: The repo.
        config: The config.
        release: The release label the repo directory is named for.
    """
    base = repo_dir(out_dir, spec, release) / "data"
    return base if spec.single_config else base / config.name


# --------------------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class HfExportResult:
    """What one :func:`export_hf` run produced."""

    out_dir: Path
    stats: Stats
    repos: list[str] = field(default_factory=list)
    cards: dict[str, Path] = field(default_factory=dict)

    def as_summary(self) -> dict[str, Any]:
        """Return a JSON-able view for the CLI's run summary."""
        return {
            "out_dir": str(self.out_dir),
            "repos": list(self.repos),
            **self.stats.as_summary(),
        }


# --------------------------------------------------------------------------------------
# Derived-repo helpers
# --------------------------------------------------------------------------------------


def _sense_of(identifier: str) -> str:
    """Return the sense id embedded at the head of a query/positive id.

    Args:
        identifier: A ``{sense_id}#...`` id, or a bare sense id.
    """
    return identifier.split("#", 1)[0]


def _lexeme_of_sense(sense_id: str) -> str:
    """Return the lexeme id of a sense id.

    Args:
        sense_id: ``{lexeme_id}:{pos}:{index}``.
    """
    return sense_id.rsplit(":", 2)[0]


def _lexeme_of_doc(doc_id: str) -> str:
    """Return the lexeme id of a retrieval document id.

    Args:
        doc_id: A sense id, or a ``{lexeme_id}:encyclopedia`` id.
    """
    parts = doc_id.split(":")
    if len(parts) >= _SENSE_ID_PARTS:
        return ":".join(parts[:-2])
    return parts[0]


def _keep(keep: set[str] | None, lexeme_id: str) -> bool:
    """Return whether a derived row's owning entry is in scope.

    Args:
        keep: The ``--from-list`` selection, or ``None`` for the whole store.
        lexeme_id: The row's owning entry.
    """
    return keep is None or lexeme_id in keep


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the objects of a JSONL file.

    Args:
        path: The file.
    """
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _span(value: Any) -> tuple[int | None, int | None]:  # noqa: ANN401 - JSON value
    """Split a ``[start, end]`` span (or ``None``) into two nullable columns.

    Args:
        value: The span as the pairs exporter wrote it.
    """
    if isinstance(value, (list, tuple)) and len(value) == _SPAN_PARTS:
        return int(value[0]), int(value[1])
    return None, None


# --------------------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------------------


def export_hf(
    store: LexemeStore,
    out_dir: Path,
    *,
    repos: str = "all",
    lexeme_ids: Sequence[str] | None = None,
    tiers_dir: Path | None = None,
    shard_rows: int = DEFAULT_SHARD_ROWS,
    max_shard_bytes: int = DEFAULT_MAX_SHARD_BYTES,
    seed: int = 0,
    easy_negatives: int = 1,
    pair_easy_negatives: int = 3,
    pretrain_levels: Sequence[ReadingLevel] = DEFAULT_PRETRAIN_LEVELS,
    owner: str = DEFAULT_OWNER,
    release: str = DEFAULT_RELEASE,
) -> HfExportResult:
    """Write the release family from ``store`` into ``out_dir``.

    Args:
        store: The store to read. Never written.
        out_dir: Root directory; one subdirectory per repo is created under it.
        repos: ``"all"`` or a comma-separated list of repo slugs.
        lexeme_ids: Restrict the export to these headwords/ids. The derived repos whose
            builders take no word list (``retrieval-triples``, ``qrels``) are built over
            the whole store and then filtered to the same set, so their *negatives* may
            still be drawn from outside it — which is deliberate, and better supervision.
        tiers_dir: Directory holding the rank TSVs (``core_10k``, ``tier2_50k``,
            ``tier3_final``, ``tier4``). A missing file is not an error: entries it would
            have named fall to a lower tier, or to ``tier = "unknown"`` when they are on
            none of them.
        shard_rows: Rows per parquet shard before rolling over.
        max_shard_bytes: Byte ceiling per shard.
        seed: Seed for the derived exporters' deterministic sampling.
        easy_negatives: Easy negatives per query in ``retrieval-triples``.
        pair_easy_negatives: Sampled cross-headword negatives per sense in
            ``retrieval-pairs``.
        pretrain_levels: Reading levels the pretraining corpus is rendered at.
        owner: Hugging Face namespace, used only for the cross-links in the cards.
        release: Release label the repos are written and named for (D-75). Pass an
            older label (e.g. ``v2.0``) to reproduce a previous release's naming.

    Returns:
        The statistics, the repos written, and where each card landed.
    """
    specs = resolve_repos(repos, release=release)
    out_dir.mkdir(parents=True, exist_ok=True)
    tiers = TierIndex.from_dir(tiers_dir)
    builder = RowBuilder(tiers)
    writers = _WriterSet(
        out_dir, specs, max_rows=shard_rows, max_bytes=max_shard_bytes, release=release
    )

    selected_ids = None if lexeme_ids is None else {slugify(word) for word in lexeme_ids}

    for entry_id in _entry_ids(store, selected_ids):
        entry = store.read(entry_id)
        if entry is None:
            continue
        for slug, config, row in builder.rows_for_entry(entry):
            writers.write(slug, config, row)
    for slug, config, row in builder.finish():
        writers.write(slug, config, row)

    stats = builder.stats
    _export_derived(
        store,
        out_dir,
        writers=writers,
        stats=stats,
        keep=selected_ids,
        lexeme_ids=lexeme_ids,
        seed=seed,
        easy_negatives=easy_negatives,
        pair_easy_negatives=pair_easy_negatives,
        pretrain_levels=pretrain_levels,
        release=release,
    )

    writers.close(stats)

    cards: dict[str, Path] = {}
    for spec in specs:
        path = repo_dir(out_dir, spec, release) / "README.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_card(spec, stats, owner=owner, release=release), encoding="utf-8")
        cards[spec.slug] = path

    return HfExportResult(
        out_dir=out_dir,
        stats=stats,
        repos=[spec.name(release) for spec in specs],
        cards=cards,
    )


def _entry_ids(store: LexemeStore, selected: set[str] | None) -> list[str]:
    """Return the entry ids to visit, in deterministic ``lexeme_id`` order.

    Args:
        store: The store.
        selected: The ``--from-list`` selection, or ``None`` for everything.
    """
    if selected is None:
        return sorted(store.iter_ids())
    return sorted(selected)


def _export_derived(
    store: LexemeStore,
    out_dir: Path,
    *,
    writers: _WriterSet,
    stats: Stats,
    keep: set[str] | None,
    lexeme_ids: Sequence[str] | None,
    seed: int,
    easy_negatives: int,
    pair_easy_negatives: int,
    pretrain_levels: Sequence[ReadingLevel],
    release: str = DEFAULT_RELEASE,
) -> None:
    """Build the four repos derived from the existing free retrieval exporters.

    Each one calls that exporter's own public function and reshapes its records; none of
    the mining logic is reimplemented here.

    Args:
        store: The store to read.
        out_dir: The export root (``qrels.trec`` is written into the qrels repo).
        writers: Where rows go.
        stats: Where the exporters' own summaries are recorded.
        keep: The ``--from-list`` selection, or ``None``.
        lexeme_ids: The raw word list, for the exporters that accept one.
        seed: Deterministic-sampling seed.
        easy_negatives: Easy negatives per query for triples.
        pair_easy_negatives: Sampled negatives per sense for pairs.
        pretrain_levels: Reading levels for the pretraining corpus.
        release: Release label the qrels repo directory is named for.
    """
    if writers.wants("retrieval-pairs"):
        _export_retrieval_pairs(
            store,
            writers=writers,
            stats=stats,
            keep=keep,
            lexeme_ids=lexeme_ids,
            seed=seed,
            easy_negatives=pair_easy_negatives,
        )
    if writers.wants("retrieval-triples"):
        _export_retrieval_triples(
            store, writers=writers, stats=stats, keep=keep, seed=seed, easy=easy_negatives
        )
    if writers.wants("qrels"):
        _export_qrels(
            store, out_dir, writers=writers, stats=stats, keep=keep, seed=seed, release=release
        )
    if writers.wants("pretrain"):
        _export_pretrain(
            store,
            writers=writers,
            stats=stats,
            keep=keep,
            lexeme_ids=lexeme_ids,
            seed=seed,
            levels=pretrain_levels,
        )


def _export_retrieval_pairs(
    store: LexemeStore,
    *,
    writers: _WriterSet,
    stats: Stats,
    keep: set[str] | None,
    lexeme_ids: Sequence[str] | None,
    seed: int,
    easy_negatives: int,
) -> None:
    """Convert the free pairs exporter's JSONL into the ``retrieval-pairs`` repo."""
    with tempfile.TemporaryDirectory(prefix="opengloss-hf-") as tmp:
        path = Path(tmp) / "pairs.jsonl"
        outcome = export_pairs(
            store, path, lexeme_ids=lexeme_ids, easy_negatives=easy_negatives, seed=seed
        )
        stats.derived_summaries["retrieval-pairs"] = dict(outcome.as_dict())
        for record in _read_jsonl(path):
            sense_a = record.get("sense_a")
            lexeme_id = _lexeme_of_sense(sense_a) if sense_a else slugify(str(record["headword"]))
            if not _keep(keep, lexeme_id):
                continue
            span_a_start, span_a_end = _span(record.get("span_a"))
            span_b_start, span_b_end = _span(record.get("span_b"))
            writers.write(
                "retrieval-pairs",
                "default",
                {
                    "headword": record["headword"],
                    "headword_b": record["headword_b"],
                    "lexeme_id": lexeme_id,
                    "sense_a": sense_a,
                    "sense_b": record.get("sense_b"),
                    "text_a": record["text_a"],
                    "text_b": record["text_b"],
                    "span_a_start": span_a_start,
                    "span_a_end": span_a_end,
                    "span_b_start": span_b_start,
                    "span_b_end": span_b_end,
                    "label": record["label"],
                    "level_a": record["level_a"],
                    "level_b": record["level_b"],
                    "kind": record["kind"],
                    "live_senses": record.get("live_senses"),
                    "tier": _tier_of(stats, lexeme_id),
                },
            )


def _export_retrieval_triples(
    store: LexemeStore,
    *,
    writers: _WriterSet,
    stats: Stats,
    keep: set[str] | None,
    seed: int,
    easy: int,
) -> None:
    """Convert the free triples exporter's result into the ``retrieval-triples`` repo."""
    result = build_triples(store, seed=seed, easy_negatives=easy)
    stats.derived_summaries["retrieval-triples"] = dict(result.as_summary())
    for triple in result.triples:
        sense_id = _sense_of(triple.query_id)
        lexeme_id = _lexeme_of_sense(sense_id)
        if not _keep(keep, lexeme_id):
            continue
        writers.write(
            "retrieval-triples",
            "default",
            {
                "query": triple.query,
                "positive": triple.positive,
                "negative": triple.negative,
                "negative_kind": triple.negative_kind,
                "query_id": triple.query_id,
                "positive_id": triple.positive_id,
                "negative_id": triple.negative_id,
                "query_source": triple.query_source,
                "sense_id": sense_id,
                "lexeme_id": lexeme_id,
                "live_senses": triple.live_senses,
                "tier": _tier_of(stats, lexeme_id),
            },
        )


def _export_qrels(
    store: LexemeStore,
    out_dir: Path,
    *,
    writers: _WriterSet,
    stats: Stats,
    keep: set[str] | None,
    seed: int,
    release: str = DEFAULT_RELEASE,
) -> None:
    """Convert the free qrels exporter's result into the ``qrels`` repo.

    The listwise queries and the document corpus become parquet configs; the judgements
    are additionally written verbatim as ``qrels.trec`` at the repo root, because that is
    the file every retrieval evaluation harness already reads.
    """
    result = build_qrels(store, seed=seed)
    stats.derived_summaries["qrels"] = dict(result.as_summary())

    kept_query_ids: set[str] = set()
    for query in result.listwise:
        sense_id = _sense_of(query.query_id)
        lexeme_id = _lexeme_of_sense(sense_id)
        if not _keep(keep, lexeme_id):
            continue
        kept_query_ids.add(query.query_id)
        writers.write(
            "qrels",
            "listwise",
            {
                "query_id": query.query_id,
                "query": query.query,
                "query_source": query.query_source,
                "sense_id": sense_id,
                "lexeme_id": lexeme_id,
                "candidates": [
                    {"id": candidate.id, "text": candidate.text, "grade": candidate.grade}
                    for candidate in query.candidates
                ],
                "n_candidates": len(query.candidates),
                "tier": _tier_of(stats, lexeme_id),
            },
        )

    for doc_id, text in sorted(result.docs.items()):
        lexeme_id = _lexeme_of_doc(doc_id)
        if not _keep(keep, lexeme_id):
            continue
        writers.write(
            "qrels",
            "docs",
            {
                "doc_id": doc_id,
                "text": text,
                "lexeme_id": lexeme_id,
                "tier": _tier_of(stats, lexeme_id),
            },
        )

    spec = REPOS_BY_SLUG["qrels"]
    trec_path = repo_dir(out_dir, spec, release) / "qrels.trec"
    trec_path.parent.mkdir(parents=True, exist_ok=True)
    with trec_path.open("w", encoding="utf-8") as handle:
        for entry in result.qrels:
            if keep is not None and entry.query_id not in kept_query_ids:
                continue
            handle.write(f"{entry.as_trec_line()}\n")


def _export_pretrain(
    store: LexemeStore,
    *,
    writers: _WriterSet,
    stats: Stats,
    keep: set[str] | None,
    lexeme_ids: Sequence[str] | None,
    seed: int,
    levels: Sequence[ReadingLevel],
) -> None:
    """Convert the free pretraining exporter's JSONL into the ``pretrain`` repo."""
    with tempfile.TemporaryDirectory(prefix="opengloss-hf-") as tmp:
        path = Path(tmp) / "pretrain.jsonl"
        summary = export_pretrain(
            store,
            path,
            templates=PRETRAIN_TEMPLATES,
            levels=levels,
            seed=seed,
            lexeme_ids=lexeme_ids,
        )
        stats.derived_summaries["pretrain"] = dict(summary.as_dict())
        for record in _read_jsonl(path):
            lexeme_id = str(record["id"]).split("#", 1)[0]
            if not _keep(keep, lexeme_id):
                continue
            writers.write(
                "pretrain",
                "default",
                {
                    "id": record["id"],
                    "lexeme_id": lexeme_id,
                    "headword": record["headword"],
                    "template": record["template"],
                    "level": record["level"],
                    "level_used": record["level_used"],
                    "text": record["text"],
                    "n_words": record["n_words"],
                    "tier": _tier_of(stats, lexeme_id),
                },
            )


def _tier_of(stats: Stats, lexeme_id: str) -> str:
    """Return a derived row's tier, reusing the tier the store pass already assigned.

    The store pass is the authority: it read the rank lists once and stamped a tier on
    every entry it exported. Looking the tier up again from the index here would work but
    would let a derived row disagree with its own entry's row if the two were ever built
    from different lists.

    Args:
        stats: The statistics object; its tier lookup is populated by the store pass.
        lexeme_id: The entry id.
    """
    return stats.tier_lookup.get(lexeme_id, "unknown")


# --------------------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------------------


def push_repos(
    out_dir: Path,
    specs: Iterable[RepoSpec],
    *,
    owner: str = DEFAULT_OWNER,
    release: str = DEFAULT_RELEASE,
    private: bool = False,
    api: Any = None,  # noqa: ANN401 - huggingface_hub.HfApi, injected for tests
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Create each repo if it does not exist and upload its local directory.

    This is the only function in the module that touches the network, and it is reached
    only through ``export-hf --push``. It uses ``HfApi.upload_large_folder``, which
    resumes and parallelises — the right tool for a directory of multi-hundred-megabyte
    parquet shards, and the wrong one for anything the CLI does by default.

    Args:
        out_dir: The export root the directories were written under.
        specs: The repos to upload.
        owner: The namespace to publish under.
        release: The release label the repos were written and named for.
        private: Whether a newly created repo starts private.
        api: An ``HfApi``-shaped object. Injected by the tests; when ``None``, a real
            ``huggingface_hub.HfApi`` is built, which is also the only place this package
            imports that dependency.
        token: Hugging Face token, when building the API here.

    Returns:
        One record per repo: its id, its local directory, and the resulting URL.
    """
    if api is None:
        from huggingface_hub import HfApi  # noqa: PLC0415 - optional, network-only path

        api = HfApi(token=token)

    pushed: list[dict[str, Any]] = []
    for spec in specs:
        repo_id = spec.repo_id(owner, release)
        folder = repo_dir(out_dir, spec, release)
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
        api.upload_large_folder(repo_id=repo_id, repo_type="dataset", folder_path=str(folder))
        pushed.append(
            {
                "repo_id": repo_id,
                "folder": str(folder),
                "url": f"https://huggingface.co/datasets/{repo_id}",
            }
        )
    return pushed
