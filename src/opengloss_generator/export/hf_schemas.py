"""The v2.0 Hugging Face release registry: one :class:`RepoSpec` per dataset repo (D-72).

This module is the single source of truth for *what the release contains*. Every
consumer reads it rather than restating it:

* :mod:`opengloss_generator.export.hf` builds the ``pyarrow`` schema of each config
  straight from its :class:`FieldSpec` tuple, so a column can never be written with an
  inferred type or with a type that disagrees with the documentation;
* :mod:`opengloss_generator.export.hf_cards` renders each config's fields table, and the
  family table that appears in every card, from the same tuples — so a field added here
  shows up in the parquet file *and* in the card, or in neither.

The release is a **family of repos, not one repo** (D-72). Two of them are canonical and
nested (``lexicon``, ``senses``: one row per lexeme / per live sense, with the renditions,
relations, queries and QA pairs inline as ``list<struct>``); the rest are flat, one row
per item, each carrying the join keys (``lexeme_id``, ``sense_id``, ``headword``, ``pos``,
``tier``) so it is usable on its own without a join back to a nested repo. v1.3 shipped
seven repos and its flat query-examples set was the most downloaded of them, which is the
evidence this layout is built on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "ALL_REPO_SLUGS",
    "DEFAULT_RELEASE",
    "PLACEHOLDER_RELEASE",
    "REPOS",
    "REPOS_BY_SLUG",
    "ConfigSpec",
    "FieldSpec",
    "RepoSpec",
    "repo_dir_name",
    "resolve_repos",
]

#: The release label these repos publish under by default. Part of every repo name
#: (D-72); overridable per export via ``--release`` (D-75) so an older label (``v2.0``)
#: stays reproducible after the default moves on.
DEFAULT_RELEASE = "v2.1"

#: The token a repo's own ``blurb``/``snippet`` text is authored with in place of a
#: literal release, wherever it names *another* member of this same family. Rendering
#: substitutes this for the release actually being exported (D-75), so a card built with
#: ``--release v2.0`` never tells a reader to load ``opengloss-v2.1-senses``. Never a real
#: release string itself, so the substitution cannot collide with genuine content.
PLACEHOLDER_RELEASE = "vX"

#: The default Hugging Face namespace the repos are published under.
DEFAULT_OWNER = "mjbommar"


# --------------------------------------------------------------------------------------
# Specification types
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One column: its name, its explicit arrow type, and its one-line documentation.

    Attributes:
        name: The column name, as it appears in the parquet file.
        arrow: The exact ``pyarrow`` type. Never inferred — a nested column is spelled
            out as ``list_(struct(...))`` here so the written file's schema is fixed by
            this module rather than by whichever row happened to be written first.
        description: The sentence the dataset card's fields table shows. Written for a
            consumer who has never read this repository.
    """

    name: str
    arrow: pa.DataType
    description: str

    @property
    def type_label(self) -> str:
        """Return a compact, human-readable rendering of :attr:`arrow` for the card."""
        return _type_label(self.arrow)


@dataclass(frozen=True, slots=True)
class ConfigSpec:
    """One Hugging Face dataset *config* within a repo: a schema plus its files.

    A repo with a single config writes its shards to ``data/train-*.parquet``; a repo
    with several writes each config's shards to ``data/<config>/train-*.parquet``. Both
    shapes are named explicitly in the card's ``configs:`` YAML block, so the
    ``datasets`` library never has to guess.

    Attributes:
        name: The config name (``"default"`` for a single-config repo).
        grain: "one row per ..." — what a row of this config *is*.
        fields: The columns, in written order.
    """

    name: str
    grain: str
    fields: tuple[FieldSpec, ...]

    @property
    def schema(self) -> pa.Schema:
        """Return the explicit ``pyarrow`` schema for this config."""
        return pa.schema([pa.field(f.name, f.arrow) for f in self.fields])

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return the column names in written order."""
        return tuple(f.name for f in self.fields)


@dataclass(frozen=True, slots=True)
class RepoSpec:
    """One dataset repo in the v2.0 family.

    Attributes:
        slug: The short name used by ``--repos`` and as the local output directory's
            suffix (e.g. ``senses`` for ``opengloss-v2.0-senses``).
        summary: One sentence for the family table every card carries.
        blurb: The card's own three-to-four sentence summary paragraph.
        configs: The configs, in card order. The first one supplies the example row.
        task_categories: Hugging Face task tags for the YAML front matter.
        tags: Free-form tags for the YAML front matter, appended to the shared set.
        snippet_title: Heading for the repo's task-specific code sample.
        snippet: The task-specific code sample itself (already a fenced block body).
        extra_files: Plain files this repo carries beside its parquet shards, as
            ``(filename, one-line description)`` — currently only ``qrels.trec``.
    """

    slug: str
    summary: str
    blurb: str
    configs: tuple[ConfigSpec, ...]
    task_categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    snippet_title: str = ""
    snippet: str = ""
    extra_files: tuple[tuple[str, str], ...] = ()

    def name(self, release: str = DEFAULT_RELEASE) -> str:
        """Return the repo name without an owner, e.g. ``opengloss-v2.1-senses``.

        Args:
            release: The release label to publish under.
        """
        return f"opengloss-{release}-{self.slug}"

    def repo_id(self, owner: str = DEFAULT_OWNER, release: str = DEFAULT_RELEASE) -> str:
        """Return the fully qualified Hugging Face repo id.

        Args:
            owner: The namespace to publish under.
            release: The release label to publish under.
        """
        return f"{owner}/{self.name(release)}"

    @property
    def single_config(self) -> bool:
        """Return whether this repo has exactly one (``default``) config."""
        return len(self.configs) == 1 and self.configs[0].name == "default"

    def config(self, name: str) -> ConfigSpec:
        """Return the named config.

        Args:
            name: The config name.

        Raises:
            KeyError: When this repo has no such config.
        """
        for cfg in self.configs:
            if cfg.name == name:
                return cfg
        raise KeyError(f"{self.name()} has no config {name!r}")

    def data_glob(self, config: ConfigSpec) -> str:
        """Return the ``data_files`` glob for one of this repo's configs.

        Args:
            config: The config whose shards to address.
        """
        if self.single_config:
            return "data/train-*.parquet"
        return f"data/{config.name}/train-*.parquet"


def repo_dir_name(spec: RepoSpec, release: str = DEFAULT_RELEASE) -> str:
    """Return the local output directory name for a repo.

    Args:
        spec: The repo.
        release: The release label to publish under.
    """
    return spec.name(release)


def resolve_repos(selector: str, release: str = DEFAULT_RELEASE) -> list[RepoSpec]:
    """Return the repos ``--repos`` selects.

    Args:
        selector: ``"all"`` or a comma-separated list of slugs (``senses,queries``).
            Full repo names (``opengloss-v2.1-senses``) are accepted too.
        release: The release label a full repo name is matched against.

    Returns:
        The selected repos, in registry order (never in the order they were typed, so
        an export is deterministic whatever the caller writes).

    Raises:
        ValueError: When a name matches no repo.
    """
    if selector.strip().lower() == "all":
        return list(REPOS)
    wanted: set[str] = set()
    for token in selector.split(","):
        name = token.strip()
        if not name:
            continue
        slug = name.removeprefix(f"opengloss-{release}-")
        if slug not in REPOS_BY_SLUG:
            known = ", ".join(ALL_REPO_SLUGS)
            raise ValueError(f"unknown repo {name!r}; choose from: all, {known}")
        wanted.add(slug)
    if not wanted:
        raise ValueError("--repos selected nothing")
    return [spec for spec in REPOS if spec.slug in wanted]


def _type_label(arrow: pa.DataType) -> str:
    """Return a short label for an arrow type, for the card's fields table.

    Args:
        arrow: The type to render.

    Returns:
        ``string``, ``int32``, ``list<string>``, ``list<struct<a, b>>``, ``struct<a, b>``
        — deep struct members are named but their own types are not, which is what keeps
        a fields table readable when a column holds eight-member structs.
    """
    if pa.types.is_list(arrow) or pa.types.is_large_list(arrow):
        return f"list<{_type_label(arrow.value_type)}>"
    if pa.types.is_struct(arrow):
        members = ", ".join(field.name for field in arrow)
        return f"struct<{members}>"
    if pa.types.is_timestamp(arrow):
        return "timestamp[us, UTC]"
    return str(arrow)


# --------------------------------------------------------------------------------------
# Reusable column groups and nested types
# --------------------------------------------------------------------------------------

_STR = pa.string()
_I32 = pa.int32()
_I64 = pa.int64()
_F64 = pa.float64()
_BOOL = pa.bool_()


def _lexeme_keys() -> tuple[FieldSpec, ...]:
    """Return the join keys every lexeme-grained flat repo carries."""
    return (
        FieldSpec("lexeme_id", _STR, "Entry id: `slugify(headword)`. Join key across the family."),
        FieldSpec("headword", _STR, "The entry's surface headword."),
        FieldSpec(
            "tier",
            _STR,
            "`core` (top 10K by composite frequency), `tier2` (ranks to ~42K), `tier3` "
            "(the rest of the frequency-ranked single words), `tier4` (stopwords, plus "
            "compounds and names at Wikipedia frequency ≥ 10) or `unknown` (on none of "
            "the rank lists); an export may contain only some of these — see the "
            "coverage table.",
        ),
    )


def _sense_keys() -> tuple[FieldSpec, ...]:
    """Return the join keys every sense-grained flat repo carries."""
    return (
        FieldSpec("sense_id", _STR, "Sense id: `{lexeme_id}:{pos}:{index}`. Join key."),
        FieldSpec("lexeme_id", _STR, "Owning entry id: `slugify(headword)`. Join key."),
        FieldSpec("headword", _STR, "The owning entry's surface headword."),
        FieldSpec("pos", _STR, "Part of speech of the owning POS entry (`noun`, `verb`, …)."),
        FieldSpec("sense_index", _I32, "Zero-based position of the sense within its POS entry."),
        FieldSpec("domain", _STR, "Controlled domain leaf, `root.leaf` (nullable)."),
        FieldSpec(
            "tier",
            _STR,
            "`core`, `tier2`, `tier3`, `tier4` or `unknown`; see the coverage table for "
            "which of these an export actually contains.",
        ),
    )


_ETYMOLOGY_SEGMENT = pa.struct(
    [
        pa.field("language", _STR),
        pa.field("language_code", _STR),
        pa.field("form", _STR),
        pa.field("meaning", _STR),
        pa.field("era", _STR),
    ]
)

_ETYMOLOGY = pa.struct(
    [
        pa.field("summary", _STR),
        pa.field("segments", pa.list_(_ETYMOLOGY_SEGMENT)),
        pa.field("cognates", pa.list_(_STR)),
        pa.field("references", pa.list_(_STR)),
    ]
)

_MORPHOLOGY = pa.struct(
    [
        pa.field("pos", _STR),
        pa.field("plural", _STR),
        pa.field("past_tense", _STR),
        pa.field("past_participle", _STR),
        pa.field("present_participle", _STR),
        pa.field("third_person_singular", _STR),
        pa.field("comparative", _STR),
        pa.field("superlative", _STR),
        pa.field("derivations", pa.list_(_STR)),
        pa.field("collocations", pa.list_(_STR)),
    ]
)

_PROSE_RENDITION = pa.struct(
    [
        pa.field("reading_level", _STR),
        pa.field("register", _STR),
        pa.field("text", _STR),
        pa.field("readability_grade", _F64),
    ]
)

_EXAMPLE_RENDITION = pa.struct(
    [
        pa.field("text", _STR),
        pa.field("span_start", _I32),
        pa.field("span_end", _I32),
        pa.field("reading_level", _STR),
        pa.field("register", _STR),
        pa.field("readability_grade", _F64),
        pa.field("source", _STR),
    ]
)

_RELATION = pa.struct(
    [
        pa.field("type", _STR),
        pa.field("target_term", _STR),
        pa.field("target_lexeme_id", _STR),
        pa.field("target_sense_id", _STR),
        pa.field("confidence", _F64),
        pa.field("note", _STR),
    ]
)

_QUERY = pa.struct(
    [
        pa.field("style", _STR),
        pa.field("text", _STR),
        pa.field("headword_free", _BOOL),
    ]
)

_QA_PAIR = pa.struct(
    [
        pa.field("question", _STR),
        pa.field("answer", _STR),
        pa.field("question_type", _STR),
        pa.field("difficulty", _STR),
        pa.field("grounded_in", pa.list_(_STR)),
    ]
)

_CONTRAST = pa.struct(
    [
        pa.field("edge_id", _STR),
        pa.field("target_sense_id", _STR),
        pa.field("verdict", _STR),
        pa.field("text", _STR),
    ]
)

_PROVENANCE_SUMMARY = pa.struct(
    [
        pa.field("models", pa.list_(_STR)),
        pa.field("total_cost_usd", _F64),
        pa.field("n_records", _I32),
    ]
)

_LISTWISE_CANDIDATE = pa.struct(
    [
        pa.field("id", _STR),
        pa.field("text", _STR),
        pa.field("grade", _I32),
    ]
)


# --------------------------------------------------------------------------------------
# The repos
# --------------------------------------------------------------------------------------

_LEXICON = RepoSpec(
    slug="lexicon",
    summary="One row per lexeme: kind, morphology, etymology, encyclopedia, contrasts, "
    "sense ids, provenance summary.",
    blurb=(
        "The entry-level view of OpenGloss v2.0: one row per lexeme, with everything "
        "that belongs to the *entry* rather than to one of its meanings — the `kind` "
        "discriminator, per-POS morphology, structured etymology, the lexical "
        "explanation, the encyclopedia article at every reading level it was written "
        'for, and the "X vs Y" contrast paragraphs written about the entry\'s symmetric '
        "relation edges. Sense-level content lives in `opengloss-vX-senses`, joined on "
        "`lexeme_id`."
    ),
    task_categories=("text-generation", "feature-extraction", "text-classification"),
    tags=("dictionary", "lexicon", "etymology", "encyclopedic", "morphology"),
    snippet_title="Entries whose encyclopedia article was written for grade 5",
    snippet="""import polars as pl

lex = pl.read_parquet("data/train-*.parquet")
grade5 = (
    lex.explode("encyclopedia")
    .filter(pl.col("encyclopedia").struct.field("reading_level") == "grade_5")
    .select("headword", pl.col("encyclopedia").struct.field("text"))
)
print(grade5.head())""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per lexeme",
            fields=(
                FieldSpec(
                    "lexeme_id", _STR, "Entry id: `slugify(headword)`. The family's join key."
                ),
                FieldSpec("headword", _STR, "The entry's surface headword."),
                FieldSpec("language", _STR, "BCP-47-ish language tag; `en` throughout v2.0."),
                FieldSpec(
                    "kind",
                    _STR,
                    "Lexeme kind discriminator: `simplex`, `compound`, `phrasal_verb`, "
                    "`idiom`, `proper_noun`, `abbreviation`, `affix`, `function_word`.",
                ),
                FieldSpec("status", _STR, "`complete`, `partial` or `retired`."),
                FieldSpec("tier", _STR, "`core`, `tier2` or `tier3` (see coverage table)."),
                FieldSpec(
                    "pos_list", pa.list_(_STR), "Parts of speech this entry has, in stored order."
                ),
                FieldSpec("sense_ids", pa.list_(_STR), "Ids of this entry's live senses."),
                FieldSpec("n_live_senses", _I32, "How many live (non-tombstoned) senses."),
                FieldSpec(
                    "n_retired_senses",
                    _I32,
                    "How many senses are tombstoned. Retired senses are kept in the store "
                    "but never exported (D-1: deletion is a tombstone, not a removal).",
                ),
                FieldSpec(
                    "is_stopword", _BOOL, "Whether the entry is a closed-class function word."
                ),
                FieldSpec("frequency", _F64, "Raw Wikipedia occurrence count, when known."),
                FieldSpec("zipf", _F64, "Zipf-scaled frequency (van Heuven), when known."),
                FieldSpec(
                    "morphology",
                    pa.list_(_MORPHOLOGY),
                    "One struct per POS entry: inflected forms, derivations, collocations.",
                ),
                FieldSpec(
                    "etymology",
                    _ETYMOLOGY,
                    "Prose summary plus the ordered language trail, cognates and references "
                    "(null when the entry has none).",
                ),
                FieldSpec(
                    "lexical_explanation",
                    pa.list_(_PROSE_RENDITION),
                    '"Why this word" prose, one struct per (reading level, register).',
                ),
                FieldSpec(
                    "encyclopedia",
                    pa.list_(_PROSE_RENDITION),
                    "The entry-level encyclopedia article, one struct per (reading level, "
                    "register). Entry-level, never a stand-in for one sense (D-71).",
                ),
                FieldSpec(
                    "contrasts",
                    pa.list_(_CONTRAST),
                    'One "X vs Y" paragraph per symmetric relation edge, with the verdict '
                    "on whether the edge is what it claims.",
                ),
                FieldSpec(
                    "provenance_summary",
                    _PROVENANCE_SUMMARY,
                    "Which models wrote this entry, what it cost, how many recorded calls.",
                ),
                FieldSpec("created_at", _STR, "ISO-8601 UTC timestamp of first generation."),
                FieldSpec("updated_at", _STR, "ISO-8601 UTC timestamp of the last write."),
            ),
        ),
    ),
)

_SENSES = RepoSpec(
    slug="senses",
    summary="One row per live sense: canonical gloss, 8 gloss renditions, examples, "
    "resolved relations, synthetic queries, grounded QA pairs.",
    blurb=(
        "The sense-level view of OpenGloss v2.0 and the repo most consumers want: one "
        "row per **live** sense, with its canonical gloss, its eight reading-level and "
        "register renditions, its sense-tagged example sentences with headword character "
        "spans, its typed relations resolved to *sense* ids rather than bare strings, its "
        "synthetic retrieval queries and its grounded question/answer pairs, all inline. "
        "Retired senses are tombstoned in the source store and never appear here. Join to "
        "`opengloss-vX-lexicon` on `lexeme_id` for entry-level content."
    ),
    task_categories=(
        "text-generation",
        "question-answering",
        "feature-extraction",
        "text-classification",
        "sentence-similarity",
    ),
    tags=("dictionary", "word-sense-disambiguation", "semantic-network", "knowledge-graph"),
    snippet_title="Every sense of a headword, with its eight renditions",
    snippet="""from datasets import load_dataset

senses = load_dataset("mjbommar/opengloss-vX-senses", split="train")
bank = senses.filter(lambda row: row["headword"] == "bank")
for row in bank:
    print(row["sense_id"], "-", row["gloss"])
    for rendition in row["gloss_renditions"]:
        print(f"   {rendition['reading_level']}/{rendition['register']}: {rendition['text']}")""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per live sense",
            fields=(
                *_sense_keys(),
                FieldSpec(
                    "upos", _STR, "Universal Dependencies POS tag for `pos` (export crosswalk)."
                ),
                FieldSpec(
                    "domain_root", _STR, "The root of `domain` (one of 15), for coarse filtering."
                ),
                FieldSpec(
                    "secondary_domains", pa.list_(_STR), "Additional domain leaves, when tagged."
                ),
                FieldSpec("gloss", _STR, "The canonical `(neutral, plain)` definition."),
                FieldSpec(
                    "gloss_renditions",
                    pa.list_(_PROSE_RENDITION),
                    "Every stored rendition of the definition, canonical included: four "
                    "reading levels and four registers.",
                ),
                FieldSpec(
                    "examples",
                    pa.list_(_EXAMPLE_RENDITION),
                    "Sense-tagged example sentences with the headword's `[span_start, "
                    "span_end)` character offsets; `source` says whether the sentence came "
                    "from the per-sense examples stage or from a rendition rewrite.",
                ),
                FieldSpec(
                    "relations",
                    pa.list_(_RELATION),
                    "Typed relations; `target_sense_id` is non-null when the target was "
                    "resolved to a sense that exists in the store.",
                ),
                FieldSpec(
                    "queries",
                    pa.list_(_QUERY),
                    "Synthetic retrieval queries across eight styles; `headword_free` marks "
                    "the ones that never name their own headword.",
                ),
                FieldSpec(
                    "qa_pairs",
                    pa.list_(_QA_PAIR),
                    "Question/answer pairs answered only from this sense's own stored text, "
                    "with the rendition ids the answer cites.",
                ),
                FieldSpec("n_examples", _I32, "Length of `examples`."),
                FieldSpec("n_relations", _I32, "Length of `relations`."),
                FieldSpec("n_queries", _I32, "Length of `queries`."),
                FieldSpec("n_qa_pairs", _I32, "Length of `qa_pairs`."),
                FieldSpec(
                    "qa_score",
                    _F64,
                    "The Opus judge's score for this sense, on the judged sample only (null "
                    "everywhere else).",
                ),
            ),
        ),
    ),
)

_DEFINITIONS = RepoSpec(
    slug="definitions",
    summary="One row per gloss rendition (canonical included): reading level, register, "
    "text, readability grade.",
    blurb=(
        "The flat definition view: one row for every stored rendition of every live "
        "sense's definition, the canonical `(neutral, plain)` gloss included. This is the "
        "reading-level and register grading of OpenGloss v2.0 laid out one row at a time, "
        "which is the shape most training and analysis code wants. Join back to "
        "`opengloss-vX-senses` on `sense_id`."
    ),
    task_categories=("text-generation", "text-classification", "feature-extraction"),
    tags=("dictionary", "definitions", "readability", "reading-level", "register"),
    snippet_title="A parallel corpus of one definition at four reading levels",
    snippet="""import duckdb

rows = duckdb.sql('''
    SELECT sense_id, reading_level, text
    FROM 'data/train-*.parquet'
    WHERE register = 'plain'
    ORDER BY sense_id, reading_level
''').df()
print(rows.head(10))""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per gloss rendition",
            fields=(
                *_sense_keys(),
                FieldSpec(
                    "reading_level",
                    _STR,
                    "`neutral`, `grade_1`, `grade_5`, `grade_10` or `college`.",
                ),
                FieldSpec(
                    "register",
                    _STR,
                    "`plain`, `informal`, `formal`, `technical` or `marketing`.",
                ),
                FieldSpec("text", _STR, "The definition at this (reading level, register)."),
                FieldSpec(
                    "readability_grade",
                    _F64,
                    "Measured Flesch-Kincaid grade of `text`, when the pipeline recorded one.",
                ),
                FieldSpec(
                    "is_canonical",
                    _BOOL,
                    "True for the one `(neutral, plain)` rendition, which is the sense's "
                    "canonical gloss.",
                ),
                FieldSpec(
                    "qa_flags",
                    pa.list_(_STR),
                    "MQM-grounded quality flags recorded on this rendition, if any.",
                ),
            ),
        ),
    ),
)

_EXAMPLES = RepoSpec(
    slug="examples",
    summary="One row per example sentence with the headword's character span, its reading "
    "level and register.",
    blurb=(
        "Every example sentence in OpenGloss v2.0, one row at a time, each tagged to the "
        "*sense* it illustrates and carrying the `[span_start, span_end)` character "
        "offsets of the headword occurrence inside it. That combination — a sentence, the "
        "sense it uses, and where the word is — is what a word-in-context or "
        "sense-disambiguation task needs and is normally paid for by annotation. `source` "
        "distinguishes the per-sense examples stage's verified sentences from "
        "reading-level and register rewrites of an existing example."
    ),
    task_categories=("text-classification", "token-classification", "feature-extraction"),
    tags=("word-sense-disambiguation", "examples", "wic", "spans"),
    snippet_title="Word-in-context items: the sentence, the sense, the span",
    snippet="""from datasets import load_dataset

ex = load_dataset("mjbommar/opengloss-vX-examples", split="train")
row = ex[0]
text, start, end = row["text"], row["span_start"], row["span_end"]
print(text[:start] + "[" + text[start:end] + "]" + text[end:])
print("sense:", row["sense_id"], "|", row["reading_level"], "/", row["register"])""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per example rendition",
            fields=(
                *_sense_keys(),
                FieldSpec("reading_level", _STR, "The rendition's reading level."),
                FieldSpec("register", _STR, "The rendition's register."),
                FieldSpec("text", _STR, "The example sentence."),
                FieldSpec(
                    "span_start",
                    _I32,
                    "Character offset where the headword occurrence starts (null when the "
                    "span could not be placed).",
                ),
                FieldSpec("span_end", _I32, "Character offset one past the occurrence's end."),
                FieldSpec(
                    "readability_grade", _F64, "Measured Flesch-Kincaid grade, when recorded."
                ),
                FieldSpec(
                    "source",
                    _STR,
                    "`per_sense` for a sentence written by the per-sense examples stage, "
                    "`renditions` for a reading-level/register rewrite of an existing one.",
                ),
            ),
        ),
    ),
)

_ENCYCLOPEDIA_FIELDS = (
    *_lexeme_keys(),
    FieldSpec("kind", _STR, "The entry's lexeme kind."),
    FieldSpec("reading_level", _STR, "The rendition's reading level."),
    FieldSpec("register", _STR, "The rendition's register."),
    FieldSpec("text", _STR, "The article or explanation at this (reading level, register)."),
    FieldSpec("readability_grade", _F64, "Measured Flesch-Kincaid grade, when recorded."),
    FieldSpec("n_words", _I32, "Whitespace-delimited word count of `text`."),
    FieldSpec("is_canonical", _BOOL, "True for the `(neutral, plain)` rendition."),
)

_ENCYCLOPEDIA = RepoSpec(
    slug="encyclopedia",
    summary="One row per encyclopedia article rendition, plus an `explanation` config "
    'for the "why this word" prose.',
    blurb=(
        "The long-form entry-level prose of OpenGloss v2.0, one row per rendition. The "
        "`encyclopedia` config holds the 300–500-word article about each headword, "
        "written at up to five reading levels; the `explanation` config holds the shorter "
        '"why this word" lexical explanation. Both are **entry-level**: they are about '
        "the headword as a whole and are never a stand-in for one sense of a polysemous "
        "entry (D-71). Etymology has its own repo, `opengloss-vX-etymology`."
    ),
    task_categories=("text-generation", "feature-extraction", "summarization"),
    tags=("encyclopedic", "long-form", "reading-level"),
    snippet_title="The same article at five reading levels",
    snippet="""from datasets import load_dataset

enc = load_dataset("mjbommar/opengloss-vX-encyclopedia", "encyclopedia", split="train")
one = enc.filter(lambda row: row["lexeme_id"] == "abseil")
for row in sorted(one, key=lambda r: r["reading_level"]):
    print(row["reading_level"], "-", row["n_words"], "words")
    print(row["text"][:200], "...\\n")""",
    configs=(
        ConfigSpec(
            name="encyclopedia",
            grain="one row per encyclopedia rendition",
            fields=_ENCYCLOPEDIA_FIELDS,
        ),
        ConfigSpec(
            name="explanation",
            grain="one row per lexical-explanation rendition",
            fields=_ENCYCLOPEDIA_FIELDS,
        ),
    ),
)

_ETYMOLOGY = RepoSpec(
    slug="etymology",
    summary="One row per entry with an etymology: prose summary, ordered language trail, "
    "cognates, references.",
    blurb=(
        "Structured word histories for OpenGloss v2.0: one row per entry that has an "
        "etymology, with a prose summary and the ordered trail of source languages, each "
        "segment carrying its language, ISO 639-3 code where one applies, attested form, "
        "meaning and era, plus cognates and reference URLs. It is a separate repo rather "
        "than columns on the encyclopedia rows because an etymology is one structured "
        "record per *entry*, not one per (reading level, register) — bolting it onto a "
        "rendition-grained table would repeat it once per level (D-72)."
    ),
    task_categories=("text-generation", "feature-extraction"),
    tags=("etymology", "historical-linguistics", "dictionary"),
    snippet_title="Entries with a Latin ancestor",
    snippet="""import polars as pl

ety = pl.read_parquet("data/train-*.parquet")
latin = ety.filter(
    pl.col("segments").list.eval(pl.element().struct.field("language") == "Latin").list.any()
)
print(latin.select("headword", "summary").head())""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per entry with an etymology",
            fields=(
                *_lexeme_keys(),
                FieldSpec("kind", _STR, "The entry's lexeme kind."),
                FieldSpec("summary", _STR, "Prose account of the word's history."),
                FieldSpec(
                    "segments",
                    pa.list_(_ETYMOLOGY_SEGMENT),
                    "The ordered language trail, oldest first: language, ISO 639-3 code "
                    "(or the `ine-pro`/`gem-pro` reconstructed-language exceptions), form, "
                    "meaning, era.",
                ),
                FieldSpec("cognates", pa.list_(_STR), "Related forms in other languages."),
                FieldSpec("references", pa.list_(_STR), "Source URLs the generator cited."),
                FieldSpec("n_segments", _I32, "Length of `segments`."),
            ),
        ),
    ),
)

_INFLECTIONS = RepoSpec(
    slug="inflections",
    summary="One row per inflected or derived form, plus the lemma itself: a flat "
    "form→lemma lookup.",
    blurb=(
        "A flat form→lemma lookup table, one row per surface string a consumer might "
        "actually type or scan: every stored inflected form (`plural`, `past_tense`, "
        "`past_participle`, `present_participle`, `third_person_singular`, "
        "`comparative`, `superlative`), every recorded `derivation`, and — critically — "
        "one `lemma` row for the headword itself, so resolving *any* surface string, "
        "inflected or not, is the same one lookup rather than a branch on whether "
        "stemming is needed first. Sourced straight from each POS entry's `morphology`, "
        "the same structure `opengloss-vX-lexicon` carries nested (D-75)."
    ),
    task_categories=("token-classification", "text-classification"),
    tags=("morphology", "inflection", "lemmatization"),
    snippet_title="Resolve a surface form to its lemma and part of speech",
    snippet="""import polars as pl

forms = pl.read_parquet("data/train-*.parquet")


def resolve(surface: str) -> pl.DataFrame:
    needle = surface.lower()
    return forms.filter(pl.col("form_normalized") == needle).select(
        "form", "lexeme_id", "headword", "pos", "relation"
    )


print(resolve("geese"))
print(resolve("Ran"))""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per inflected, derived or lemma form",
            fields=(
                FieldSpec("form", _STR, "The surface form, case preserved as stored."),
                FieldSpec(
                    "form_normalized",
                    _STR,
                    "`form.lower()` — filter on this column when the input's casing is "
                    "not known to match.",
                ),
                *_lexeme_keys(),
                FieldSpec("pos", _STR, "Part of speech of the owning POS entry."),
                FieldSpec(
                    "relation",
                    _STR,
                    "`lemma` (the headword itself), `plural`, `past_tense`, "
                    "`past_participle`, `present_participle`, `third_person_singular`, "
                    "`comparative`, `superlative` or `derivation`.",
                ),
            ),
        ),
    ),
)

_RELATIONS = RepoSpec(
    slug="relations",
    summary="One row per semantic edge, resolved to target sense ids; a `tombstoned` "
    "config recovers the edges the reconcile pass removed.",
    blurb=(
        "The OpenGloss v2.0 semantic graph as an edge list. The `relations` config holds "
        "every live typed edge — fourteen relation types — with the target resolved to a "
        "*sense* id wherever the target's entry exists in the release, which is what makes "
        "this a sense graph rather than a word graph. The `tombstoned` config recovers the "
        "edges the free reconcile pass demoted, deduplicated or capped away, with the type "
        "they carried when they were removed and the reason recorded on them, "
        "reconstructed from the provenance trail rather than kept in a side table "
        "(D-65, D-68)."
    ),
    task_categories=("text-classification", "feature-extraction"),
    tags=("knowledge-graph", "semantic-network", "wordnet", "graph"),
    snippet_title="Load the graph into networkx",
    snippet="""import networkx as nx
import polars as pl

edges = pl.read_parquet("data/relations/train-*.parquet").filter(
    (pl.col("type") == "hypernym") & pl.col("resolved")
)
graph = nx.DiGraph()
graph.add_edges_from(edges.select("source_sense_id", "target_sense_id").rows())
print(graph.number_of_nodes(), "senses,", graph.number_of_edges(), "hypernym edges")""",
    configs=(
        ConfigSpec(
            name="relations",
            grain="one row per live relation edge",
            fields=(
                FieldSpec(
                    "edge_id",
                    _STR,
                    "Derived edge id: `{source_sense_id}-{type}->{target_lexeme_id}`. Stable "
                    "across resolution, because it keys on the target's slug, not its sense.",
                ),
                FieldSpec("source_sense_id", _STR, "The sense asserting the relation."),
                FieldSpec("source_lexeme_id", _STR, "That sense's entry id."),
                FieldSpec("headword", _STR, "That entry's headword."),
                FieldSpec("pos", _STR, "Part of speech of the source sense."),
                FieldSpec(
                    "type",
                    _STR,
                    "One of `synonym`, `antonym`, `hypernym`, `hyponym`, `meronym`, "
                    "`holonym`, `derivation`, `collocation`, `confusable_with`, `see_also`, "
                    "`causes`, `entails`, `used_with`, `instance_of`.",
                ),
                FieldSpec("target_term", _STR, "The target's surface form, as written."),
                FieldSpec("target_lexeme_id", _STR, "`slugify(target_term)` — always derivable."),
                FieldSpec(
                    "target_sense_id",
                    _STR,
                    "The resolved target sense, or null when the target's entry is not in "
                    "the release.",
                ),
                FieldSpec("resolved", _BOOL, "Whether `target_sense_id` is non-null."),
                FieldSpec("confidence", _F64, "The resolver's confidence, 0–1, when resolved."),
                FieldSpec(
                    "note",
                    _STR,
                    "Free text on the edge. Required on `confusable_with` (how the two "
                    "differ); elsewhere it carries a hygiene pass's reason.",
                ),
                FieldSpec("tier", _STR, "Tier of the source entry."),
            ),
        ),
        ConfigSpec(
            name="tombstoned",
            grain="one row per removed relation edge",
            fields=(
                FieldSpec("edge_id", _STR, "The removed edge's id, reconstructed from the record."),
                FieldSpec("source_sense_id", _STR, "The sense the edge was removed from."),
                FieldSpec("source_lexeme_id", _STR, "That sense's entry id."),
                FieldSpec("headword", _STR, "That entry's headword."),
                FieldSpec(
                    "type",
                    _STR,
                    "The type the edge carried **when it was removed** — which is what its "
                    "id is built from. The pre-demotion type is only recoverable when the "
                    "reason names it (`retyped: nano synonym→see_also`).",
                ),
                FieldSpec("target_term", _STR, "The target's surface form."),
                FieldSpec("target_lexeme_id", _STR, "`slugify(target_term)`."),
                FieldSpec(
                    "step",
                    _STR,
                    "Which reconcile step removed it: `tombstone` (a demoted edge), `dedup` "
                    "(an exact duplicate) or `cap` (per-type overflow).",
                ),
                FieldSpec(
                    "reason",
                    _STR,
                    "The note the edge carried when it was removed — the demotion reason, or "
                    "`-` where it had none (usual for `cap`).",
                ),
                FieldSpec("provenance_id", _STR, "The entry provenance record this was read from."),
                FieldSpec("tier", _STR, "Tier of the source entry."),
            ),
        ),
    ),
)

_QUERIES = RepoSpec(
    slug="queries",
    summary="One row per synthetic retrieval query, across eight query styles, tagged to "
    "the sense it should retrieve.",
    blurb=(
        "Synthetic search queries written *per sense*, in eight styles — keyword, "
        "question, conversational, constraint, role, example-based, step-by-step and "
        "directive — with each sense's sibling senses in the prompt so the queries "
        "discriminate between the meanings of one headword. `headword_free` marks the "
        "queries that never name their own headword, the property that stops a retriever "
        "trained on them from collapsing into string matching. This is the doc2query side "
        "of the release; the graded relevance judgements live in `opengloss-vX-qrels`."
    ),
    task_categories=("text-retrieval", "sentence-similarity", "text-generation"),
    tags=("doc2query", "retrieval", "query-generation", "synthetic"),
    snippet_title="Query/definition pairs for a bi-encoder",
    snippet="""import polars as pl

queries = pl.read_parquet("hf://datasets/mjbommar/opengloss-vX-queries/data/train-*.parquet")
defs = pl.read_parquet(
    "hf://datasets/mjbommar/opengloss-vX-definitions/data/train-*.parquet"
).filter(pl.col("is_canonical"))
pairs = queries.join(defs.select("sense_id", "text"), on="sense_id", suffix="_gloss")
print(pairs.select("text", "text_gloss").head())""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per synthetic query",
            fields=(
                FieldSpec(
                    "query_id",
                    _STR,
                    "Derived, positional, zero-based: `{sense_id}#q{n}`. The stage only "
                    "appends, so an id never changes meaning.",
                ),
                *_sense_keys(),
                FieldSpec(
                    "style",
                    _STR,
                    "`keyword`, `question`, `conversational`, `constraint`, `role`, "
                    "`example_based`, `step_by_step` or `directive`.",
                ),
                FieldSpec("text", _STR, "The query, 1–200 characters."),
                FieldSpec(
                    "headword_free",
                    _BOOL,
                    "True when the query contains no form of its own headword (whole-word, "
                    "case-insensitive, inflections included).",
                ),
            ),
        ),
    ),
)

_QA_PAIRS = RepoSpec(
    slug="qa-pairs",
    summary="One row per grounded question/answer pair, with the rendition ids the answer cites.",
    blurb=(
        "Question/answer pairs written per sense and answerable **only** from that "
        "sense's own stored text — its gloss, its examples, its entry's encyclopedia "
        "article and etymology — with every source labelled by an id the answer has to "
        "cite. Uncited, mis-cited, ungrounded and duplicate pairs were dropped before "
        "storage. Seven question types at mixed difficulty; `grounded_in` holds the "
        "rendition ids, so a consumer can rebuild the (context, question, answer) triple "
        "by joining back to `opengloss-vX-definitions`, `-examples` or `-encyclopedia`."
    ),
    task_categories=("question-answering", "text-generation"),
    tags=("question-answering", "grounded-generation", "rag", "synthetic"),
    snippet_title="A closed-book QA set with its own context",
    snippet="""from datasets import load_dataset

qa = load_dataset("mjbommar/opengloss-vX-qa-pairs", split="train")
hard = qa.filter(lambda row: row["difficulty"] == "hard" and row["question_type"] == "reasoning")
row = hard[0]
print(row["question"])
print("->", row["answer"])
print("cites:", row["grounded_in"])""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per question/answer pair",
            fields=(
                FieldSpec("qa_id", _STR, "Derived, positional, zero-based: `{sense_id}#qa{n}`."),
                *_sense_keys(),
                FieldSpec("question", _STR, "The question, 1–500 characters."),
                FieldSpec("answer", _STR, "The answer, 1–2000 characters."),
                FieldSpec(
                    "question_type",
                    _STR,
                    "`factual`, `definition`, `reasoning`, `comparison`, `procedural`, "
                    "`causal` or `hypothetical`.",
                ),
                FieldSpec("difficulty", _STR, "`easy`, `medium` or `hard`."),
                FieldSpec(
                    "grounded_in",
                    pa.list_(_STR),
                    "Rendition ids the answer is supported by, e.g. "
                    "`projection:noun:1#neutral/plain`, `projection:noun:1#ex3`, "
                    "`projection:encyclopedia#neutral/plain`, `projection:etymology`.",
                ),
            ),
        ),
    ),
)

_CONTRASTS = RepoSpec(
    slug="contrasts",
    summary='One row per "X vs Y" paragraph on a synonym/antonym/confusable edge, with a '
    "verdict on the edge.",
    blurb=(
        "For every synonym, antonym or `confusable_with` edge whose far end resolves to a "
        "sense that is actually in the release, one 60–120 word paragraph saying **how the "
        "two terms actually differ** — the register that separates them, the axis they "
        "oppose on, the collocation that picks one over the other — plus a verdict on "
        "whether the edge is the relation it claims to be. Both headwords and both "
        "canonical glosses are on the row, so it reads standalone. A pair is written once, "
        "on the end whose sense id sorts smaller."
    ),
    task_categories=("text-generation", "text-classification"),
    tags=("lexical-semantics", "confusables", "contrastive", "synonymy"),
    snippet_title="Contrastive pairs, and the edges the writer disagreed with",
    snippet="""import duckdb

duckdb.sql('''
    SELECT verdict, count(*) AS n
    FROM 'data/train-*.parquet'
    GROUP BY verdict ORDER BY n DESC
''').show()

duckdb.sql('''
    SELECT source_headword, target_headword, text
    FROM 'data/train-*.parquet'
    WHERE relation_type = 'antonym' LIMIT 3
''').show()""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per contrast paragraph",
            fields=(
                FieldSpec(
                    "edge_id",
                    _STR,
                    "The edge this paragraph is about: "
                    "`{source_sense_id}-{relation_type}->{target_lexeme_id}`.",
                ),
                FieldSpec("source_sense_id", _STR, "The near end of the edge."),
                FieldSpec("source_lexeme_id", _STR, "The near end's entry id."),
                FieldSpec("source_headword", _STR, "The near end's headword."),
                FieldSpec("source_gloss", _STR, "The near sense's canonical gloss."),
                FieldSpec("target_sense_id", _STR, "The far end, when resolved."),
                FieldSpec("target_lexeme_id", _STR, "The far end's entry id."),
                FieldSpec("target_headword", _STR, "The far end's headword."),
                FieldSpec(
                    "target_gloss",
                    _STR,
                    "The far sense's canonical gloss, looked up across the whole release "
                    "(null when the far end is not in it).",
                ),
                FieldSpec(
                    "relation_type",
                    _STR,
                    "`synonym`, `antonym` or `confusable_with` — parsed from `edge_id`.",
                ),
                FieldSpec(
                    "verdict",
                    _STR,
                    "`related_as_typed`, `related_differently` or `unrelated`. Recorded and "
                    "counted; the relation edits it implies were applied by a separate free "
                    "pass, so a verdict here is evidence, not an unapplied edit.",
                ),
                FieldSpec("reading_level", _STR, "The paragraph's reading level."),
                FieldSpec("register", _STR, "The paragraph's register."),
                FieldSpec("text", _STR, "The contrast paragraph."),
                FieldSpec("tier", _STR, "Tier of the source entry."),
            ),
        ),
    ),
)

_PROVENANCE = RepoSpec(
    slug="provenance",
    summary="One row per recorded generation call: stage, model, tokens, cost, run id — "
    "the audit trail.",
    blurb=(
        "The audit trail for OpenGloss v2.0: one row per recorded unit of work, saying "
        "which stage ran, which model answered, how many prompt and completion tokens it "
        "used, how much of the prompt hit the provider's cache, and what it cost. Nothing "
        "in this release was written without a row here. It is what makes the cost claims "
        "in the other cards checkable rather than asserted, and it is what a reader who "
        "wants to know *which model wrote this field* should join against."
    ),
    task_categories=("text-classification",),
    tags=("provenance", "audit", "cost", "metadata"),
    snippet_title="What the release cost, by stage and model",
    snippet="""import duckdb

duckdb.sql('''
    SELECT stage, model, count(*) AS calls, round(sum(cost_usd), 2) AS usd
    FROM 'data/train-*.parquet'
    GROUP BY stage, model
    ORDER BY usd DESC
''').show()""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per provenance record",
            fields=(
                *_lexeme_keys(),
                FieldSpec(
                    "provenance_id",
                    _STR,
                    "The record's key inside its entry's provenance table (`p1`, `p2`, …), "
                    "one-based because these are dictionary keys, not list positions.",
                ),
                FieldSpec("stage", _STR, "The pipeline stage that made the call."),
                FieldSpec(
                    "model",
                    _STR,
                    "The model that answered, or `rule:<name>` for a deterministic, "
                    "zero-cost pass.",
                ),
                FieldSpec("provider", _STR, "The provider the model was routed to, when recorded."),
                FieldSpec("prompt_version", _STR, "Version of the instruction text used."),
                FieldSpec("service_tier", _STR, "Provider service tier, e.g. `flex`."),
                FieldSpec("input_tokens", _I64, "Prompt tokens reported by the provider."),
                FieldSpec("cached_input_tokens", _I64, "How many of those hit the prefix cache."),
                FieldSpec("output_tokens", _I64, "Completion tokens reported by the provider."),
                FieldSpec(
                    "cost_usd",
                    _F64,
                    "Cost computed locally from reported usage against a versioned price "
                    "table, cached input priced at the cached rate.",
                ),
                FieldSpec("attempts", _I32, "How many attempts the call took to validate."),
                FieldSpec("run_id", _STR, "The run this call belonged to."),
                FieldSpec(
                    "note",
                    _STR,
                    "The stage's idempotence marker or removal record, truncated to 500 "
                    "characters.",
                ),
                FieldSpec("generated_at", _STR, "ISO-8601 UTC timestamp of the call."),
            ),
        ),
    ),
)

_RETRIEVAL_PAIRS = RepoSpec(
    slug="retrieval-pairs",
    summary="Word-in-context and doc2query-shaped (text_a, text_b, label) pairs mined from "
    "the store for free.",
    blurb=(
        "Binary-labelled text pairs mined straight off the release with no model call: "
        "two example sentences of the *same* sense (positive), one example from each of "
        "two senses of the *same* headword (the hard word-in-context negative), an "
        "example paired with its own sense's gloss (positive), and optional sampled "
        "cross-headword same-domain negatives. Every pair carries both spans, both "
        "reading levels, and `live_senses`, so a consumer can filter or reweight by "
        "polysemy without re-deriving it."
    ),
    task_categories=("sentence-similarity", "text-classification", "feature-extraction"),
    tags=("wic", "word-sense-disambiguation", "contrastive-learning", "embeddings"),
    snippet_title="A word-in-context classification set",
    snippet="""from datasets import load_dataset

pairs = load_dataset("mjbommar/opengloss-vX-retrieval-pairs", split="train")
wic = pairs.filter(lambda row: row["kind"].startswith("wic_"))
print(wic)
row = wic[0]
print(row["label"], "|", row["text_a"], "||", row["text_b"])""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per mined pair",
            fields=(
                FieldSpec("headword", _STR, "The headword side A is about."),
                FieldSpec(
                    "headword_b",
                    _STR,
                    "Equal to `headword` except for a cross-headword easy negative.",
                ),
                FieldSpec("lexeme_id", _STR, "Entry id of side A. Join key."),
                FieldSpec("sense_a", _STR, "Side A's sense id."),
                FieldSpec(
                    "sense_b",
                    _STR,
                    "Side B's sense id; null when side B is an entry-level encyclopedia article.",
                ),
                FieldSpec("text_a", _STR, "Side A's text."),
                FieldSpec("text_b", _STR, "Side B's text."),
                FieldSpec(
                    "span_a_start", _I32, "Headword span start in `text_a`, when it has one."
                ),
                FieldSpec("span_a_end", _I32, "Headword span end in `text_a`."),
                FieldSpec(
                    "span_b_start", _I32, "Headword span start in `text_b`, when it has one."
                ),
                FieldSpec("span_b_end", _I32, "Headword span end in `text_b`."),
                FieldSpec("label", _I32, "1 for a positive pair, 0 for a negative one."),
                FieldSpec("level_a", _STR, "Reading level of side A's source rendition."),
                FieldSpec("level_b", _STR, "Reading level of side B's source rendition."),
                FieldSpec(
                    "kind",
                    _STR,
                    "`wic_positive`, `wic_hard_negative`, `wic_easy_negative`, "
                    "`example_gloss` or `example_encyclopedia`.",
                ),
                FieldSpec("live_senses", _I32, "How many live senses side A's entry has."),
                FieldSpec("tier", _STR, "Tier of side A's entry."),
            ),
        ),
    ),
)

_RETRIEVAL_TRIPLES = RepoSpec(
    slug="retrieval-triples",
    summary="MS MARCO-style (query, positive, negative) triples whose hard negatives come "
    "from the graph.",
    blurb=(
        "Training triples for an embedding model or reranker. Each row is a query, a "
        "positive passage from the sense the query was written for, and one negative. The "
        "hard negative is drawn from the *graph* by a priority-ordered fallback — another "
        "sense of the same headword, a `confusable_with` target, a co-hyponym, or a "
        "synonym-of-a-synonym — which is what makes it hard and, unusually, auditable: the "
        "contrast paragraph in `opengloss-vX-contrasts` often states in words why the "
        "negative is a negative. A direct synonym is never offered as a negative."
    ),
    task_categories=("sentence-similarity", "text-retrieval", "feature-extraction"),
    tags=("embeddings", "hard-negatives", "msmarco-style", "contrastive-learning"),
    snippet_title="Train a bi-encoder with MultipleNegativesRankingLoss",
    snippet="""from datasets import load_dataset
from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, losses

triples = load_dataset("mjbommar/opengloss-vX-retrieval-triples", split="train")
# MultipleNegativesRankingLoss expects exactly (anchor, positive, negative) columns.
triples = triples.select_columns(["query", "positive", "negative"]).rename_column(
    "query", "anchor"
)

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
trainer = SentenceTransformerTrainer(
    model=model,
    train_dataset=triples,
    loss=losses.MultipleNegativesRankingLoss(model),
)
trainer.train()""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per (query, positive, negative) triple",
            fields=(
                FieldSpec("query", _STR, "The query text."),
                FieldSpec("positive", _STR, "A passage the query should retrieve."),
                FieldSpec("negative", _STR, "A passage it should not."),
                FieldSpec(
                    "negative_kind",
                    _STR,
                    "`other_sense`, `confusable`, `co_hyponym`, `synonym_of_synonym` or `easy`.",
                ),
                FieldSpec(
                    "query_id",
                    _STR,
                    "`{sense_id}#q{n}` for a written query, or the id of the gloss "
                    "rendition standing in for one.",
                ),
                FieldSpec(
                    "positive_id",
                    _STR,
                    "A sense id, a `{sense_id}#example` id, or a `{lexeme_id}:encyclopedia` id.",
                ),
                FieldSpec("negative_id", _STR, "Same id space as `positive_id`."),
                FieldSpec(
                    "query_source",
                    _STR,
                    "`generated` for a written query, `gloss_pseudo` when a gloss rendition "
                    "stood in — so pseudo-queries can be filtered or reweighted.",
                ),
                FieldSpec("sense_id", _STR, "The sense the query belongs to."),
                FieldSpec("lexeme_id", _STR, "That sense's entry id. Join key."),
                FieldSpec("live_senses", _I32, "Live sense count of the query's own entry."),
                FieldSpec("tier", _STR, "Tier of the query's entry."),
            ),
        ),
    ),
)

_QRELS = RepoSpec(
    slug="qrels",
    summary="Graded TREC relevance judgements (0–3) plus the document corpus and listwise "
    "candidate lists.",
    blurb=(
        "A ready-to-score retrieval benchmark built from the release's own graph. The "
        "`listwise` config gives one query with its whole graded candidate list; the "
        "`docs` config is the document corpus those candidate ids address; and "
        "`qrels.trec` at the repo root is the same judgements in standard `trec_eval` "
        "format. Grades are 3 (the query's own sense), 2 (a direct synonym), 1 (a direct "
        "hypernym, a co-hyponym, or a polysemous entry's encyclopedia article) and 0 "
        "(everything else). The tiers are disjoint by construction: no document is offered "
        "twice at two grades for one query."
    ),
    task_categories=("text-retrieval", "sentence-similarity"),
    tags=("trec", "qrels", "retrieval", "benchmark", "reranking"),
    snippet_title="Score a run with pytrec_eval",
    snippet="""import pytrec_eval
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    "mjbommar/opengloss-vX-qrels", "qrels.trec", repo_type="dataset"
)
with open(path, encoding="utf-8") as handle:
    qrels = {}
    for line in handle:
        qid, _, docid, grade = line.split()
        qrels.setdefault(qid, {})[docid] = int(grade)

# run = {query_id: {doc_id: score}} from your own retriever
evaluator = pytrec_eval.RelevanceEvaluator(qrels, {"ndcg_cut_10", "recall_100"})
results = evaluator.evaluate(run)""",
    extra_files=(
        (
            "qrels.trec",
            "The graded judgements in standard `trec_eval` format: "
            "`query_id 0 doc_id grade`, one per line.",
        ),
    ),
    configs=(
        ConfigSpec(
            name="listwise",
            grain="one row per query, with its whole graded candidate list",
            fields=(
                FieldSpec("query_id", _STR, "The query's id; matches column 1 of `qrels.trec`."),
                FieldSpec("query", _STR, "The query text."),
                FieldSpec("query_source", _STR, "`generated` or `gloss_pseudo`."),
                FieldSpec("sense_id", _STR, "The sense the query belongs to. Join key."),
                FieldSpec("lexeme_id", _STR, "That sense's entry id."),
                FieldSpec(
                    "candidates",
                    pa.list_(_LISTWISE_CANDIDATE),
                    "The graded candidate documents: `id` into the `docs` config, `text`, "
                    "and `grade` 0–3.",
                ),
                FieldSpec("n_candidates", _I32, "Length of `candidates`."),
                FieldSpec("tier", _STR, "Tier of the query's entry."),
            ),
        ),
        ConfigSpec(
            name="docs",
            grain="one row per document in the retrieval corpus",
            fields=(
                FieldSpec(
                    "doc_id",
                    _STR,
                    "A sense id (the document is that sense's canonical gloss) or a "
                    "`{lexeme_id}:encyclopedia` id.",
                ),
                FieldSpec("text", _STR, "The document text."),
                FieldSpec("lexeme_id", _STR, "The entry the document belongs to. Join key."),
                FieldSpec("tier", _STR, "Tier of that entry."),
            ),
        ),
    ),
)

_PRETRAIN = RepoSpec(
    slug="pretrain",
    summary="Entries serialised into plain-prose dictionary, thesaurus, encyclopedia and "
    "usage-note documents.",
    blurb=(
        "The release rendered as continuous prose for language-model pretraining or "
        "continued pretraining: four document templates per entry — a dictionary entry, a "
        "thesaurus entry, an encyclopedia article and a usage note — written as plain text "
        "and light markdown, with no JSON or YAML duplication and no special tokens. A "
        "section with nothing to say is left out rather than emitted empty. Requesting a "
        "reading level renders every template at that level, falling back to the canonical "
        "text where a rendition is missing (`level_used` says which happened)."
    ),
    task_categories=("text-generation", "fill-mask"),
    tags=("pretraining", "corpus", "plain-text", "reading-level"),
    snippet_title="Stream the corpus without downloading it",
    snippet="""from datasets import load_dataset

corpus = load_dataset("mjbommar/opengloss-vX-pretrain", split="train", streaming=True)
for doc in corpus.take(3):
    print(f"--- {doc['template']} @ {doc['level']} ({doc['n_words']} words)")
    print(doc["text"][:400])""",
    configs=(
        ConfigSpec(
            name="default",
            grain="one row per rendered document",
            fields=(
                FieldSpec(
                    "id",
                    _STR,
                    "Derived: `{lexeme_id}#pretrain-{template}-{level}`, recomputable from "
                    "the row alone.",
                ),
                FieldSpec("lexeme_id", _STR, "The entry the document renders. Join key."),
                FieldSpec("headword", _STR, "That entry's headword."),
                FieldSpec(
                    "template",
                    _STR,
                    "`dictionary`, `thesaurus`, `encyclopedia` or `usage_note`.",
                ),
                FieldSpec("level", _STR, "The reading level requested for this document."),
                FieldSpec(
                    "level_used",
                    _STR,
                    "`neutral` when any part of the document fell back to canonical text; "
                    "otherwise equal to `level`.",
                ),
                FieldSpec("text", _STR, "The document."),
                FieldSpec("n_words", _I32, "Whitespace-delimited word count."),
                FieldSpec("tier", _STR, "Tier of the entry."),
            ),
        ),
    ),
)


#: Every repo in the v2.0 release family, in card and export order. The two canonical
#: nested repos come first; the flat per-item views follow in the order a reader meets
#: the content; the derived training sets come last.
REPOS: tuple[RepoSpec, ...] = (
    _LEXICON,
    _SENSES,
    _DEFINITIONS,
    _EXAMPLES,
    _ENCYCLOPEDIA,
    _ETYMOLOGY,
    _INFLECTIONS,
    _RELATIONS,
    _QUERIES,
    _QA_PAIRS,
    _CONTRASTS,
    _PROVENANCE,
    _RETRIEVAL_PAIRS,
    _RETRIEVAL_TRIPLES,
    _QRELS,
    _PRETRAIN,
)

#: Registry keyed by slug, for ``--repos`` resolution.
REPOS_BY_SLUG: dict[str, RepoSpec] = {spec.slug: spec for spec in REPOS}

#: Every slug ``--repos`` accepts, in registry order.
ALL_REPO_SLUGS: tuple[str, ...] = tuple(spec.slug for spec in REPOS)

#: The repos whose rows are read straight off the store in the single streaming pass,
#: as opposed to the four derived from the existing free exporters.
STORE_REPO_SLUGS: frozenset[str] = frozenset(
    {
        "lexicon",
        "senses",
        "definitions",
        "examples",
        "encyclopedia",
        "etymology",
        "inflections",
        "relations",
        "queries",
        "qa-pairs",
        "contrasts",
        "provenance",
    }
)

#: The repos derived from the free retrieval exporters rather than from the store pass.
DERIVED_REPO_SLUGS: frozenset[str] = frozenset(
    {"retrieval-pairs", "retrieval-triples", "qrels", "pretrain"}
)


def iter_configs() -> Iterator[tuple[RepoSpec, ConfigSpec]]:
    """Yield every ``(repo, config)`` pair in the family, in registry order."""
    for spec in REPOS:
        for config in spec.configs:
            yield spec, config
