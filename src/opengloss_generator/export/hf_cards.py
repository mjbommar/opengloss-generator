"""Dataset-card rendering for the v2.0 Hugging Face release.

One function, :func:`render_card`, turns a :class:`~opengloss_generator.export.hf_schemas.RepoSpec`
plus the :class:`~opengloss_generator.export.hf_rows.Stats` an export produced into a
finished ``README.md``. Everything numeric in a card is a live count from that export —
row counts, coverage by tier, histograms, the example row — because a card that is typed
by hand drifts from its data on the first re-run and nobody notices. The prose that is
*not* numeric (what the release is, how ids compose, what the reading levels mean, what
is wrong with it) is shared here, so sixteen cards cannot disagree with each other.

Templates are plain f-strings: no Jinja, no template files to keep in step with the
package, and a rendering bug is a Python error rather than a silently empty section.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from opengloss_generator.export.hf_rows import (
    COVERAGE_FEATURES,
    SOURCE_WORDNET,
    TIER_DESCRIPTIONS,
    TIER_TIER5,
    TIER_TIER6,
    TIER_UNKNOWN,
)
from opengloss_generator.export.hf_schemas import (
    DEFAULT_OWNER,
    DEFAULT_RELEASE,
    PLACEHOLDER_RELEASE,
    REPOS,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from opengloss_generator.export.hf_rows import Stats
    from opengloss_generator.export.hf_schemas import RepoSpec

__all__ = ["V13", "V20", "V21", "V22", "V23", "render_card"]


# --------------------------------------------------------------------------------------
# Fixed facts about the release, and about v1.3, that no export can compute
# --------------------------------------------------------------------------------------


class V13:
    """Published v1.3 figures, for the honest scope comparison every card carries.

    Taken from the v1.3 definition-level dataset card, not re-derived: they describe a
    release this pipeline did not produce and cannot recount.
    """

    LEXEMES = 205_988
    SENSES = 565_604
    EDGES = 8_479_875
    URL = "https://huggingface.co/datasets/mjbommar/opengloss-v1.3-definitions"


#: The Opus judge's mean scores on fixed 40-entry samples, out of 100 (``docs/QA-DIARY.md``
#: iterations 12 and 14): the tier-2 sample at the close of goal 2 (68.6 -> 70.2 over that
#: goal) and the tier-3 sample after its text-only recipe. Sample statistics, not
#: guarantees about any single entry.
JUDGE_SCORE = 70.2
JUDGE_SCORE_TIER3 = 66.7
JUDGE_SAMPLE_ENTRIES = 40

#: Synonym / antonym reciprocity over the whole 54,724-entry store, closing audit of
#: 2026-09-05 after tier 3 (``opengloss audit``).
SYNONYM_RECIPROCITY = 0.942
ANTONYM_RECIPROCITY = 0.943

#: Senses left with no relation at all in that audit (of 137,314 live) — the largest
#: known gap.
SENSES_WITHOUT_RELATIONS = 4_524

PAPER_URL = "https://arxiv.org/abs/2511.18622"
LICENSE_ID = "cc-by-4.0"
LICENSE_NAME = "Creative Commons Attribution 4.0 International (CC-BY 4.0)"

#: Hugging Face ``source_datasets`` front-matter values (D-80): the release's own
#: content plus the outside lexicon a tier-5 entry may be derived from.
SOURCE_DATASETS: tuple[str, ...] = ("princeton-wordnet-3.0", "opengloss-v1.3")

#: Where the WordNet 3.0 licence terms live, for the "Sources and licences" section
#: every card carries (D-78, D-80).
WORDNET_LICENSE_URL = "https://wordnet.princeton.edu/license-and-commercial-use"
WORDNET_LICENSE_NOTICE = (
    "This software and database is being provided to you, the LICENSEE, by Princeton "
    "University under the following license. By obtaining, using and/or copying this "
    "software and database, you agree that you have read, understood, and will comply "
    "with these terms and conditions. Permission to use, copy, modify and distribute "
    "this software and database and its documentation for any purpose and without fee "
    "or royalty is hereby granted, provided that you agree to comply with the following "
    "copyright notice and statements, including the disclaimer, and that the same "
    "appear on ALL copies of the software, database and documentation, including "
    'modifications that you make for internal use or for distribution. "WordNet 3.0 '
    'Copyright 2006 by Princeton University. All rights reserved."'
)

#: Tags every card carries, before the repo's own.
SHARED_TAGS: tuple[str, ...] = ("opengloss", "synthetic", "lexicography", "english")


# --------------------------------------------------------------------------------------
# Small formatting helpers
# --------------------------------------------------------------------------------------


def _n(value: int | float) -> str:
    """Return a thousands-separated integer string.

    Args:
        value: The number.
    """
    return f"{round(value):,}"


def _pct(share: float | None) -> str:
    """Return a percentage, or an em dash when there is nothing to divide by.

    Args:
        share: A fraction in ``[0, 1]``, or ``None``.
    """
    if share is None:
        return "—"
    return f"{share * 100:.1f}%"


def _mb(size: int) -> str:
    """Return a byte count in MB, to one decimal.

    Args:
        size: Bytes.
    """
    return f"{size / 1_000_000:.1f} MB"


def _size_category(rows: int) -> str:
    """Return the Hugging Face ``size_categories`` bucket for a row count.

    Args:
        rows: Total rows in the repo.
    """
    bounds = (
        (1_000, "n<1K"),
        (10_000, "1K<n<10K"),
        (100_000, "10K<n<100K"),
        (1_000_000, "100K<n<1M"),
        (10_000_000, "1M<n<10M"),
        (100_000_000, "10M<n<100M"),
    )
    for limit, label in bounds:
        if rows < limit:
            return label
    return "100M<n<1B"


def _table(headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    """Return a markdown table.

    Args:
        headers: Column headings.
        rows: Body rows, already stringified.
    """
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join([head, rule, body]) if rows else "\n".join([head, rule])


def _histogram_table(heading: tuple[str, str], counter: dict[str, int], *, limit: int = 12) -> str:
    """Return a two-column count table, largest first.

    Args:
        heading: The two column headings.
        counter: The counts.
        limit: How many rows to show before collapsing the tail into "other".
    """
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    rows = [(f"`{name}`", _n(count)) for name, count in items[:limit]]
    tail = items[limit:]
    if tail:
        rows.append((f"_{len(tail)} more_", _n(sum(count for _, count in tail))))
    return _table(heading, rows)


def _truncate(value: str, limit: int) -> str:
    """Return a string cut to ``limit`` characters with a visible marker.

    Args:
        value: The string.
        limit: Maximum characters to keep.
    """
    if len(value) <= limit:
        return value
    return f"{value[:limit]} … [truncated for this card]"


def _shorten(value: Any, *, text_limit: int = 320, list_limit: int = 3) -> Any:  # noqa: ANN401
    """Return a row value shortened enough to read inside a card.

    Long prose (an encyclopedia article runs to 500 words) and long nested lists (a sense
    can carry twelve queries) are cut, with the cut marked, so the example stays an
    example rather than becoming the document.

    Args:
        value: Any JSON-able value from a written row.
        text_limit: Maximum characters of any one string.
        list_limit: Maximum members of any one list.
    """
    if isinstance(value, str):
        return _truncate(value, text_limit)
    if isinstance(value, list):
        shortened = [
            _shorten(item, text_limit=text_limit, list_limit=list_limit)
            for item in value[:list_limit]
        ]
        if len(value) > list_limit:
            shortened.append(f"… {len(value) - list_limit} more of {len(value)}")
        return shortened
    if isinstance(value, dict):
        return {
            key: _shorten(item, text_limit=text_limit, list_limit=list_limit)
            for key, item in value.items()
        }
    return value


def _example_block(row: dict[str, Any] | None) -> str:
    """Return the fenced JSON block showing one real row.

    Args:
        row: The first row written for a config, or ``None`` when the config is empty.
    """
    if not row:
        return (
            "_This config wrote no rows in this export, so there is no example to show "
            "(see the statistics above)._"
        )
    payload = {key: _shorten(value) for key, value in row.items()}
    return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```"


# --------------------------------------------------------------------------------------
# Shared sections
# --------------------------------------------------------------------------------------


def _front_matter(spec: RepoSpec, stats: Stats) -> str:
    """Return the YAML front matter, including the ``configs:`` block.

    Args:
        spec: The repo.
        stats: The export's statistics, for ``size_categories``.
    """
    lines = [
        "---",
        f"license: {LICENSE_ID}",
        "language:",
        "- en",
        "source_datasets:",
        *(f"- {value}" for value in SOURCE_DATASETS),
        "size_categories:",
        f"- {_size_category(stats.rows_for(spec.slug))}",
    ]
    if spec.task_categories:
        lines.append("task_categories:")
        lines.extend(f"- {value}" for value in spec.task_categories)
    lines.append("tags:")
    lines.extend(f"- {value}" for value in dict.fromkeys((*SHARED_TAGS, *spec.tags)))
    lines.append("configs:")
    for config in spec.configs:
        lines.append(f"- config_name: {config.name}")
        lines.append("  data_files:")
        lines.append("  - split: train")
        lines.append(f"    path: {spec.data_glob(config)}")
    lines.append("---")
    return "\n".join(lines)


class V20:
    """Frozen statistics of the previous release (v2.0, 2026-09-05), for the changelog."""

    LEXEMES = 54_724
    LIVE_SENSES = 137_314
    MULTIWORD = 86  # compounds + phrasal verbs + idioms
    PROPER_NOUNS = 10_365
    FUNCTION_WORDS = 114
    GLOSS_RENDITIONS = 1_129_975
    EXAMPLES = 1_398_297
    RELATIONS = 735_318
    QUERIES = 1_330_311
    QA_PAIRS = 750_348
    PRETRAIN_DOCS = 617_175
    PRETRAIN_WORDS = 196_390_946
    PRETRAIN_TOKENS = 275_659_096  # cl100k_base
    JUDGE = "70.2 (core + tier 2), 66.7 (tier 3)"


class V21:
    """Measured once at release time; not derivable from the export alone.

    ``LEXEMES``/``LIVE_SENSES`` are the full-store figures from the run that actually
    produced this release (D-77's post-fix measurement: "109,633 lexemes and 250,003
    live senses"), not a sample — the same run ``PRETRAIN_DOCS`` below is measured from.
    """

    LEXEMES = 109_633
    LIVE_SENSES = 250_003
    MULTIWORD = 36_366  # compound 33,959 + phrasal verb 875 + idiom 331 + affix/other
    PROPER_NOUNS = 17_073
    FUNCTION_WORDS = 462
    GLOSS_RENDITIONS = 1_684_865
    EXAMPLES = 2_163_329
    RELATIONS = 1_574_438
    QUERIES = 1_304_650
    QA_PAIRS = 736_010
    PRETRAIN_DOCS = 1_111_044
    PRETRAIN_WORDS = 331_888_239
    PRETRAIN_TOKENS = 471_451_693  # cl100k_base
    JUDGE = "70.2 (core + tier 2), 66.7 (tier 3), 67.0 (tier 4)"


class V22:
    """v2.2's own release-time facts (D-80).

    ``TIER5_CANDIDATES``, ``INFLECTION_FOLDED``, ``FRAGMENTS_RETIRED`` and
    ``RETIRED_LEXEMES`` are already measured (the tier-5 candidate list on disk, and the
    whole-store `lexeme-hygiene` sweep D-79 shipped but had not yet run against
    production). ``PRETRAIN_DOCS``/``PRETRAIN_WORDS``/``PRETRAIN_TOKENS``/``JUDGE`` are
    not derivable before the finished pretraining corpus exists and the Opus judge has
    scored a fresh tier-5 sample, so they stay ``None`` here — **never fill them by
    guessing**; :func:`_changelog_v21_v22` refuses to render v2.2's changelog while any
    of them still is, exactly so a `v2.2` export cannot ship with an invented number.
    """

    #: The store as v2.2 shipped it (docs/NAMED-ENTITY-PLAN.md's opening measurement,
    #: 2026-09-08), for the *previous* column of the next release's size table — the same
    #: role :class:`V21`'s ``LEXEMES``/``LIVE_SENSES`` play in v2.2's own.
    LEXEMES = 148_292
    LIVE_SENSES = 288_304

    #: `data/core/tier5.tsv` row count (D-78): the WordNet 3.0 gap the earlier tiers
    #: lacked, before slugification and matching against the store.
    TIER5_CANDIDATES = 43_652
    #: `lexeme-hygiene`'s whole-store sweep (D-79): inflected-form headwords folded onto
    #: their lemma, and multiword fragments retired for beginning or ending on a
    #: function word.
    INFLECTION_FOLDED = 4_377
    FRAGMENTS_RETIRED = 172
    #: Lexemes left with zero live senses store-wide after D-76's phantom-POS whole-block
    #: retirements and D-79's fold (this export's own figure is `stats.retired_lexemes`;
    #: this is the whole-store count at the time of the sweep).
    RETIRED_LEXEMES = 4_549

    PRETRAIN_DOCS: int | None = 1_458_684  # fill at release
    PRETRAIN_WORDS: int | None = 398_029_628  # fill at release
    PRETRAIN_TOKENS: int | None = 565_384_746  # fill at release, cl100k_base
    JUDGE: str | None = (
        "70.2 (core + tier 2), 66.7 (tier 3), 67.0 (tier 4), 81.3 (tier 5)"  # fill at release
    )
<<<<<<< HEAD
=======


class V23:
    """v2.3's own release-time facts (D-81), unmeasured until the tier has been built.

    The same contract :class:`V22` states, one release on. ``TIER6_CANDIDATES`` is
    already known — it is the row count of the candidate list on disk, produced by
    ``scripts/build_tier6_candidates.py`` and fixed at 15,000 by the size decision
    recorded in D-81 — and everything below it is a *measurement of a release that does
    not exist yet*: how many of those candidates became entries, how many proper nouns
    the ``entity_type`` pass typed, how many alias edges the alias pass wrote, and what
    the Opus judge scored a fresh tier-6 sample. **Never fill them by guessing.**
    :func:`_changelog_v22_v23` refuses to render v2.3's changelog while any of them is
    still ``None``, exactly so a `v2.3` export cannot ship with an invented number, and
    :data:`~opengloss_generator.export.hf_schemas.DEFAULT_RELEASE` stays `v2.2` until
    they are all filled.
    """

    #: `data/core/tier6_candidates.tsv` row count (D-81): the ranked named-entity
    #: candidate list, before slugification and matching against the store.
    TIER6_CANDIDATES = 15_000

    TIER6_LEXEMES: int | None = None  # fill at release: entries the tier actually added
    ENTITY_TYPED: int | None = None  # fill at release: proper nouns given a real type
    ALIAS_EDGES: int | None = None  # fill at release: `alias_of` edges written
    PRETRAIN_DOCS: int | None = None  # fill at release
    PRETRAIN_WORDS: int | None = None  # fill at release
    PRETRAIN_TOKENS: int | None = None  # fill at release, cl100k_base
    JUDGE: str | None = None  # fill at release


#: :class:`V23` attributes that must be measured against the finished release before a
#: `v2.3` card can render (D-81) — see :class:`V23`'s own docstring for why. The same
#: shape as :data:`_V22_PLACEHOLDERS`, deliberately, so one reader understands both.
_V23_PLACEHOLDERS: tuple[str, ...] = (
    "TIER6_LEXEMES",
    "ENTITY_TYPED",
    "ALIAS_EDGES",
    "PRETRAIN_DOCS",
    "PRETRAIN_WORDS",
    "PRETRAIN_TOKENS",
    "JUDGE",
)


@dataclass(frozen=True, slots=True)
class _V23Facts:
    """:class:`V23`'s release-time facts, typed without the ``| None`` they carry.

    The shape :func:`_require_v22_filled` returns as a tuple, as a record instead: seven
    values is past the point where positional unpacking stays readable.
    """

    tier6_lexemes: int
    entity_typed: int
    alias_edges: int
    pretrain_docs: int
    pretrain_words: int
    pretrain_tokens: int
    judge: str


def _require_v23_filled() -> _V23Facts:
    """Return :class:`V23`'s release-time facts, once every one of them is filled.

    Returns:
        The facts, narrowed to their non-optional types by the check below.

    Raises:
        ValueError: Naming every :class:`V23` attribute that is still ``None`` — its
            ``# fill at release`` comment says what each one needs.
    """
    values = [getattr(V23, name) for name in _V23_PLACEHOLDERS]
    missing = [name for name, value in zip(_V23_PLACEHOLDERS, values, strict=True) if value is None]
    if missing:
        raise ValueError(
            "hf_cards.V23 is not filled in: "
            + ", ".join(missing)
            + " must be measured against the finished v2.3 release before its cards can "
            "render (see each attribute's '# fill at release' comment)."
        )
    lexemes, typed, aliases, docs, words, tokens, judge = values
    return _V23Facts(lexemes, typed, aliases, docs, words, tokens, judge)
>>>>>>> tier6/schema


#: :class:`V22` attributes that must be measured against the finished release before a
#: `v2.2` card can render (D-80) — see :class:`V22`'s own docstring for why.
_V22_PLACEHOLDERS: tuple[str, ...] = (
    "PRETRAIN_DOCS",
    "PRETRAIN_WORDS",
    "PRETRAIN_TOKENS",
    "JUDGE",
)


def _require_v22_filled() -> tuple[int, int, int, str]:
    """Return :class:`V22`'s four release-time facts, once every one of them is filled.

    Args:
        None.

    Returns:
        ``(pretrain_docs, pretrain_words, pretrain_tokens, judge)``, typed without the
        ``| None`` their class attributes carry, once the check below has passed.

    Raises:
        ValueError: Naming every :class:`V22` attribute that is still ``None`` — its
            ``# fill at release`` comment says what each one needs.
    """
    docs, words, tokens = V22.PRETRAIN_DOCS, V22.PRETRAIN_WORDS, V22.PRETRAIN_TOKENS
    judge = V22.JUDGE
    values = (docs, words, tokens, judge)
    missing = [name for name, value in zip(_V22_PLACEHOLDERS, values, strict=True) if value is None]
    if missing:
        raise ValueError(
            "hf_cards.V22 is not filled in: "
            + ", ".join(missing)
            + " must be measured against the finished v2.2 release before its cards can "
            "render (see each attribute's '# fill at release' comment)."
        )
    if docs is None or words is None or tokens is None or judge is None:  # pragma: no cover
        raise ValueError("unreachable: already checked above")
    return docs, words, tokens, judge


def _changelog_v20_v21(stats: Stats) -> str:
    """Return the "what changed since v2.0" section: size, entry types, tokens, schema.

    Args:
        stats: The export's statistics (the current release's live counts).
    """
    size = _table(
        ("", "v2.0 (2026-09-05)", "v2.1 (2026-09-07)"),
        [
            ("Lexemes", _n(V20.LEXEMES), _n(V21.LEXEMES)),
            ("Live senses", _n(V20.LIVE_SENSES), _n(V21.LIVE_SENSES)),
            (
                "Multiword entries (compounds, phrasal verbs, idioms)",
                _n(V20.MULTIWORD),
                _n(V21.MULTIWORD),
            ),
            ("Proper nouns", _n(V20.PROPER_NOUNS), _n(V21.PROPER_NOUNS)),
            ("Function words", _n(V20.FUNCTION_WORDS), _n(V21.FUNCTION_WORDS)),
            ("Gloss renditions", _n(V20.GLOSS_RENDITIONS), _n(V21.GLOSS_RENDITIONS)),
            ("Example sentences", _n(V20.EXAMPLES), _n(V21.EXAMPLES)),
            ("Live relations", _n(V20.RELATIONS), _n(V21.RELATIONS)),
            ("Synthetic queries", _n(V20.QUERIES), _n(V21.QUERIES)),
            ("QA pairs", _n(V20.QA_PAIRS), _n(V21.QA_PAIRS)),
            ("Pretraining documents", _n(V20.PRETRAIN_DOCS), _n(V21.PRETRAIN_DOCS)),
            ("Pretraining words", _n(V20.PRETRAIN_WORDS), _n(V21.PRETRAIN_WORDS)),
            ("Pretraining tokens (cl100k_base)", _n(V20.PRETRAIN_TOKENS), _n(V21.PRETRAIN_TOKENS)),
            ("Judge score, Opus, 40-entry samples", V20.JUDGE, V21.JUDGE),
        ],
    )
    return f"""## What changed since v2.0

v2.0 (2026-09-05) covered the frequency-ranked single words. vX adds **tier 4**: the
function words the core ranking had excluded on purpose, and every remaining v1.3 entry
at Wikipedia frequency ≥ 10 — mostly multiword compounds ("natural selection",
"catalog number"), plus names and rarer single words. That doubles the lexeme count and
changes the mix: v2.0 was 99.8% single words; a third of vX is multiword.

{size}

**Schema.** No column was added, removed or retyped in any existing dataset. Three
things did change:

- `tier` gains the value `tier4` (it was `core`, `tier2` or `tier3`).
- One new dataset, `opengloss-vX-inflections`: a flat surface-form → lemma lookup
  (plural, past tense, participles, comparative, superlative, derivations) built from
  the morphology that the lexicon already carried nested.
- New provenance note prefixes on tombstones and edges, all reversible and all counted
  in the store audit: `phantom_pos:` (a v1.3 part-of-speech block whose glosses defined
  a component word rather than the compound — 11,440 blocks retired), `regen:`
  (relations regenerated for senses that had lost every edge to judging), and
  `retyped: contrast` (synonym edges the contrast paragraphs showed to be hypernym or
  hyponym).

**Not row-compatible with v2.0.** Lexeme, sense, rendition, edge, query and QA ids are
stable for every entry v2.0 had. The derived training sets (`retrieval-pairs`,
`retrieval-triples`, `qrels`) re-sample negatives over the larger pool, so their rows
differ; and the store-wide quality passes run for vX retired ~3,000 senses of the v2.0
entries (phantom part-of-speech blocks and near-duplicate senses), so those senses are
now tombstoned rather than live. Treat vX as a new release, not a delta.

"""


def _changelog_v21_v22(stats: Stats) -> str:
    """Return the "what changed since v2.1" section: tier 5, the fold, provenance, source.

    Raises before rendering anything while :class:`V22` still carries an unfilled
    placeholder (D-80) — see :func:`_require_v22_filled`.

    Args:
        stats: The export's statistics (the current release's live counts).
    """
    pretrain_docs, pretrain_words, pretrain_tokens, judge = _require_v22_filled()
    tier5_lexemes = stats.lexemes_by_tier.get(TIER_TIER5, 0)
    wordnet_imported = V22.TIER5_CANDIDATES - 5_126
    size = _table(
        ("", "v2.1 (2026-09-07)", "v2.2"),
        [
            ("Lexemes", _n(V21.LEXEMES), _n(stats.lexemes)),
            ("Live senses", _n(V21.LIVE_SENSES), _n(stats.live_senses)),
            ("Tier 5 lexemes (WordNet gap)", "0", _n(tier5_lexemes)),
            ("Retired lexemes (every sense tombstoned)", "0", _n(stats.retired_lexemes)),
            ("Pretraining documents", _n(V21.PRETRAIN_DOCS), _n(pretrain_docs)),
            ("Pretraining words", _n(V21.PRETRAIN_WORDS), _n(pretrain_words)),
            ("Pretraining tokens (cl100k_base)", _n(V21.PRETRAIN_TOKENS), _n(pretrain_tokens)),
            ("Judge score, Opus, 40-entry samples", V21.JUDGE, judge),
        ],
    )
    return f"""## What changed since v2.1

v2.1 (2026-09-07) added tier 4 and the `inflections` repo. v2.2 adds **tier 5**:
{_n(V22.TIER5_CANDIDATES)} WordNet 3.0 candidate lemmas the earlier tiers lacked — common
compounds and technical nouns, adjectives, adverbs and verbs, instances/taxa/organisms
excluded — {_n(wordnet_imported)} of them imported outright, the rest matched against
v1.3's own files. The other three changes are about honesty rather than coverage:

- **The lemma fold.** `lexeme-hygiene` (D-79) folded {_n(V22.INFLECTION_FOLDED)}
  inflected-form headwords onto the lemma that already carried their meaning
  ("databases" onto "database", through the store's own recorded morphology) and
  retired {_n(V22.FRAGMENTS_RETIRED)} multiword fragments that began or ended on a
  function word ("is not", "on top of"). Together with D-76's phantom part-of-speech
  retirements, {_n(V22.RETIRED_LEXEMES)} lexemes store-wide now have every sense
  tombstoned. A lexeme like that is **not counted as a lexeme** anywhere in this card or
  in `Stats` any more — it has no live sense, so it is not a lexeme by this release's own
  count — but it is not gone: its surface form still resolves through
  `opengloss-v2.2-inflections`, and its `lexicon` row carries `retired = true` with a
  `retired_reason` explaining why.
- **Provenance on inherited fields.** Every field a migration or import wrote, not only
  what a model wrote from scratch, now carries a `migrate`-stage provenance record
  naming where it came from, so "where did this text come from" is answerable by
  `grep` rather than by trusting the pipeline that happened to run.
- **A `source` column** on `lexicon` and `senses`: `opengloss-v1.3` for content this
  project generated or migrated from its own legacy releases, `wordnet-3.0` for the
  tier-5 entries imported directly from Princeton WordNet 3.0.

{size}

**Schema.** No column was removed or retyped. `lexicon` gains `source`, `retired` and
`retired_reason`; `senses` gains `source`; `tier` gains the value `tier5`.

"""


def _changelog_v22_v23(stats: Stats) -> str:
    """Return the "what changed since v2.2" section: tier 6, entity types, aliases (D-81).

    A stub in the sense that its *numbers* are not measured yet — it raises through
    :func:`_require_v23_filled` until they are — not in the sense that its content is
    provisional: the four changes it names are the ones this release is, and each is
    already implemented.

    Args:
        stats: The export's statistics (the current release's live counts).
    """
    facts = _require_v23_filled()
    # v2.2's own release-time facts are the "previous release" column here, and they are
    # already guarded: a v2.3 card cannot render on a v2.2 that never finished measuring.
    prev_docs, prev_words, prev_tokens, prev_judge = _require_v22_filled()
    tier6_lexemes = stats.lexemes_by_tier.get(TIER_TIER6, 0)
    size = _table(
        ("", "v2.2 (2026-09-07)", "v2.3"),
        [
            ("Lexemes", _n(V22.LEXEMES), _n(stats.lexemes)),
            ("Live senses", _n(V22.LIVE_SENSES), _n(stats.live_senses)),
            ("Tier 6 lexemes (named entities)", "0", _n(tier6_lexemes)),
            ("Pretraining documents", _n(prev_docs), _n(facts.pretrain_docs)),
            ("Pretraining words", _n(prev_words), _n(facts.pretrain_words)),
            ("Pretraining tokens (cl100k_base)", _n(prev_tokens), _n(facts.pretrain_tokens)),
            ("Judge score, Opus, 40-entry samples", prev_judge, facts.judge),
        ],
    )
    return f"""## What changed since v2.2

v2.2 (2026-09-07) added tier 5, the WordNet 3.0 gap. v2.3 adds **tier 6**: named
entities. Every tier before it was selected by word frequency or by WordNet membership,
and neither signal ranks a name — a name's importance is a fact about the world, not
about a corpus — so v2.2 knew *Washington* and *Lincoln* but not **George Washington**,
**New York City** or **World War II**. Tier 6 is {_n(V23.TIER6_CANDIDATES)} candidates
ranked by Wikipedia vital-article level, Wikidata sitelink count, WordNet instance
membership and US salience, of which {_n(facts.tier6_lexemes)} became entries. Three
schema changes come with it:

- **Entity types are written rather than defaulted.** Every proper noun in v2.2 carried
  `entity_type = other`, because the two migrations and the kind classifier all wrote
  that placeholder and nothing ever replaced it. {_n(facts.entity_typed)} proper nouns now
  carry a real type — `person`, `place`, `organization`, `work`, `event`, `product`,
  `species` — taken from the candidate list where it knew one and bought as a single
  batched verdict where it did not. `lexicon` and `senses` gain an `entity_type` column,
  and `lexicon` gains `wikidata_qid`, the join key for reconciling an entry against
  Wikidata.
- **Aliases.** A name has variants — *Lincoln* for *Abraham Lincoln*, *the Netherlands*
  for *Netherlands*, *FDR*, *NASA* — and v2.2 had nowhere to put them. A variant with no
  entry of its own is now a member of `lexicon`'s `aliases` column and an `alias` row in
  `opengloss-v2.3-inflections`, so resolving any surface string stays one lookup; a
  variant that *does* have an entry is an `alias_of` edge in `opengloss-v2.3-relations`
  ({_n(facts.alias_edges)} of them). An `alias_of` edge is never demoted, pruned, capped
  or re-judged by the hygiene passes, unlike every other relation type.
- **Two new domain leaves.** `nature.settlements` (cities, towns, villages,
  neighbourhoods) and `law_government.polities` (countries, states, provinces, empires,
  historical polities). A quarter of tier 6 is a settlement or a polity and the taxonomy
  had no leaf for either; adding a `geography` root would have been a breaking change to
  a fixed 15-root vocabulary, so both went under roots that already exist.

{size}

**Schema.** No column was removed or retyped. `lexicon` gains `entity_type`,
`wikidata_qid` and `aliases`; `senses` gains `entity_type`; `relations` gains the
`alias_of` type; `inflections` gains the `alias` relation; `tier` gains the value
`tier6`; and the domain taxonomy gains two leaves (taxonomy version 3).

"""


def _changelog(stats: Stats, release: str) -> str:
    """Return every "what changed" section this release carries, newest first.

    Args:
        stats: The export's statistics.
        release: The release label being rendered. Only `v2.3` gets the v2.2 -> v2.3
            section (D-81), `v2.2` and `v2.3` get the v2.1 -> v2.2 one, and every release
            keeps the v2.0 -> v2.1 section (D-75's own reproducibility promise did not
            extend to dropping history from the card).
    """
    sections = []
    if release == "v2.3":
        sections.append(_changelog_v22_v23(stats))
    if release in {"v2.2", "v2.3"}:
        sections.append(_changelog_v21_v22(stats))
    sections.append(_changelog_v20_v21(stats))
    return "".join(sections)


def _whats_new(stats: Stats, release: str) -> str:
    """Return the "what's new in vX" section, with the honest scope note.

    Args:
        stats: The export's statistics.
        release: The release label being rendered (D-80): only `v2.2` gets the
            v2.1 -> v2.2 changelog section.
    """
    scope = _table(
        ("", "v1.3", "vX"),
        [
            ("Lexemes", _n(V13.LEXEMES), _n(stats.lexemes)),
            ("Senses", _n(V13.SENSES), _n(stats.live_senses)),
            (
                "Definition renditions per sense",
                "1 canonical",
                "1 canonical + up to 8 graded",
            ),
            ("Relation targets", "bare strings", "resolved to sense ids"),
            ("Retrieval training data", "companion sets", "queries, QA, triples, qrels"),
            ("Per-field provenance", "no", "model, tokens and cost per call"),
        ],
    )
    return f"""## What's new in vX vs v1.3

1. **Schema v3.** Every lexeme carries a `kind` discriminator (simplex, compound, phrasal
   verb, idiom, proper noun, abbreviation, affix, function word); every sense carries a
   controlled domain leaf from a fixed ~160-leaf taxonomy instead of free text; every
   example carries the character span of the headword occurrence inside it.
2. **Renditions, not one string.** A definition is a *set*: the canonical one plus
   rewrites at four reading levels and in four registers, each produced in a single call
   from the canonical text so they say the same thing at different altitudes.
3. **A sense graph, not a word graph.** Typed relations resolve to *sense* ids wherever
   the target's entry exists in the release, so `bank --hypernym--> financial institution`
   points at a meaning rather than at a string.
4. **Retrieval data is first-class.** Synthetic per-sense queries in eight styles,
   grounded QA pairs, mined word-in-context pairs, MS MARCO-style triples with
   graph-derived hard negatives, and graded TREC qrels — all derivable from, and
   consistent with, the same entries.
5. **Derivable identifiers everywhere.** v1.3 published a positional id for lexemes and
   senses (`3d_model_noun_0`) and nothing below that. vX gives every rendition, edge,
   query, QA pair and provenance record an id computable from the row alone, and never
   renumbers: a retired sense is tombstoned, so the ids after it keep their meaning.
6. **Per-field provenance.** Which model wrote a field, how many tokens it took, what it
   cost — published as its own dataset.

{_changelog(stats, release)}### Scope: fewer headwords, far more per headword

vX is **not** a superset of v1.3. It covers {_n(stats.lexemes)} of v1.3's {_n(V13.LEXEMES)}
lexemes — every frequency-ranked single word, plus the compounds and names at Wikipedia
frequency ≥ 10 — and spends the difference on depth.
If you need breadth of vocabulary, use
[v1.3]({V13.URL}); if you need graded renditions, resolved
relations, spans, or retrieval supervision, use vX.

{scope}
"""


def _release_stats(stats: Stats) -> str:
    """Return the release-wide statistics block every card shares.

    Whatever tiers this export actually contains — never a hard-coded three or four — get
    a one-line description followed by the lexeme/sense counts table (D-75).

    Args:
        stats: The export's statistics.
    """
    tiers = stats.tiers_present
    descriptions = "\n".join(
        f"- `{tier}` — {TIER_DESCRIPTIONS[tier]}" for tier in tiers if tier in TIER_DESCRIPTIONS
    )
    tier_rows = [
        (
            f"`{tier}`",
            _n(stats.lexemes_by_tier.get(tier, 0)),
            _n(stats.senses_by_tier.get(tier, 0)),
        )
        for tier in tiers
    ]
    table = _table(("Tier", "Lexemes", "Live senses"), tier_rows)
    return f"{descriptions}\n\n{table}" if descriptions else table


def _coverage_table(stats: Stats) -> str:
    """Return the per-tier coverage table — the release's central caveat, in numbers.

    Args:
        stats: The export's statistics.
    """
    tiers = stats.tiers_present
    headers = ("Field", "Of", *(f"`{tier}`" for tier in tiers))
    rows: list[tuple[str, ...]] = []
    for feature in COVERAGE_FEATURES:
        label = feature.label + (f" ({feature.note})" if feature.note else "")
        shares = tuple(
            _pct(stats.coverage_share(feature.key, feature.grain, tier)) for tier in tiers
        )
        rows.append((label, feature.grain, *shares))
    return _table(headers, rows)


def _ids_section() -> str:
    """Return the identifier section: how every id in the family composes."""
    return """## Identifiers, and how they compose

Every id is **derived from structure**, never randomly minted, so a consumer can
recompute one from a row and join across the whole family without a lookup table. Sense
positions are stable across regenerations: a retired sense is tombstoned, not removed, so
the indices after it never shift.

| Id | Shape | Example |
|---|---|---|
| Lexeme | `slugify(headword)` | `abseil` |
| Sense | `{lexeme_id}:{pos}:{index}` (zero-based) | `abseil:verb:0` |
| Rendition | `{owner_id}#{reading_level}/{register}` | `abseil:verb:0#grade_5/plain` |
| Entry-level owner | `{lexeme_id}:encyclopedia` / `:explanation` | `abseil:encyclopedia` |
| Edge | `{source_sense_id}-{type}->{target_lexeme_id}` | `abseil:verb:0-synonym->rappel` |
| Query | `{sense_id}#q{n}` (zero-based) | `abseil:verb:0#q3` |
| QA pair | `{sense_id}#qa{n}` (zero-based) | `abseil:verb:0#qa3` |
| Provenance record | `p{n}` within its entry (one-based) | `p12` |

An edge id keys on the *target's slug*, not on the target's sense, so resolving a target
never changes the id of the edge that found it.
"""


def _levels_section() -> str:
    """Return the reading-level and register reference tables."""
    return """## Reading levels and registers

A rendition is keyed on a `(reading_level, register)` pair. The canonical rendition of
every field is `(neutral, plain)`; everything else is a rewrite of it.

| `reading_level` | Who it is written for | Rough CCSS band |
|---|---|---|
| `neutral` | The canonical text: an adult general reader, no level targeted | — |
| `grade_1` | Beginning readers; short sentences, common words | K–1 |
| `grade_5` | Upper elementary | 4–5 |
| `grade_10` | Secondary | 9–10 |
| `college` | Undergraduate and above; technical vocabulary allowed | 11–CCR |

| `register` | What changes | Reading it |
|---|---|---|
| `plain` | Nothing — the neutral register | The default |
| `informal` | Conversational, contractions, everyday words | How you'd say it to a friend |
| `formal` | Full forms, precise hedging, no contractions | How you'd write it in a report |
| `technical` | Domain vocabulary, exact conditions | How a specialist would state it |
| `marketing` | Benefit-first, persuasive framing | A genre, not a formality level |

`marketing` sits on the register axis for convenience but is a *genre* value rather than
a point on the formality scale — worth remembering if you train a formality classifier on
this column.
"""


def _family_table(current: RepoSpec, owner: str, release: str) -> str:
    """Return the family table, identical in every card, with the current repo marked.

    Args:
        current: The repo whose card this is.
        owner: The Hugging Face namespace.
        release: The release label the repos in the table are named for.
    """
    rows: list[tuple[str, ...]] = []
    for spec in REPOS:
        grains = " · ".join(config.grain for config in spec.configs)
        if spec.slug == current.slug:
            name = f"**`{spec.name(release)}`** (this one)"
        else:
            name = (
                f"[`{spec.name(release)}`]"
                f"(https://huggingface.co/datasets/{spec.repo_id(owner, release)})"
            )
        rows.append((name, grains, spec.summary))
    return _table(("Dataset", "Grain", "What it holds"), rows)


def _join_ticked(names: Sequence[str]) -> str:
    """Return backtick-quoted names, joined with "and" the way a sentence would.

    Args:
        names: The names to quote and join. Never empty.
    """
    ticked = [f"`{name}`" for name in names]
    if len(ticked) == 1:
        return ticked[0]
    return ", ".join(ticked[:-1]) + f" and {ticked[-1]}"


def _partial_tiers(stats: Stats) -> list[str]:
    """Return the present tiers (``unknown`` excluded) that did not receive every stage.

    A tier counts as partial when it falls short of full coverage on any of the
    stage-defining features that the text-only passes never ran — queries, QA pairs, and
    contrasts. Whichever tiers this export's own coverage numbers say are partial are the
    ones named; nothing here assumes there are three tiers, or that tier 3 is the one
    that is short (D-75).

    Args:
        stats: The export's statistics.
    """
    grains = {feature.key: feature.grain for feature in COVERAGE_FEATURES}
    partial: list[str] = []
    for tier in stats.tiers_present:
        if tier == TIER_UNKNOWN:
            continue
        full = all(
            (stats.coverage_share(feature, grains[feature], tier) or 0.0) >= 1.0
            for feature in ("queries", "qa", "contrasts")
        )
        if not full:
            partial.append(tier)
    return partial


def _limitations(spec: RepoSpec, stats: Stats, release: str) -> str:
    """Return the known-limitations section, tailored where the repo differs.

    Args:
        spec: The repo.
        stats: The export's statistics.
        release: The release label, for the cross-link to the ``senses`` repo.
    """
    partial = _partial_tiers(stats)
    if partial:
        partial_lexemes = sum(stats.lexemes_by_tier.get(tier, 0) for tier in partial)
        names = _join_ticked(partial)
        verb = "is" if len(partial) == 1 else "are"
        tier_note = (
            f"- **{names} {verb} deliberately partial.** {_n(partial_lexemes)} lexemes "
            f"across {names} received the text stages (glosses, examples, encyclopedia) "
            "but not the queries, QA pairs, contrasts or register renditions. The coverage "
            "table above gives the exact per-field share; nothing is hidden behind an "
            "average."
        )
    else:
        tier_note = (
            "- **Every tier in this export received every stage.** Nothing here was built "
            "in a text-only pass, so the coverage table above is the whole story for what "
            "is in it."
        )
    extra = ""
    if spec.slug in {"queries", "qa-pairs", "contrasts"} and partial:
        names = _join_ticked(partial)
        that_tier = "That tier" if len(partial) == 1 else "Those tiers"
        extra = (
            f"\n- **This repo excludes {names}.** {that_tier} never ran this stage, so "
            "its senses are absent here entirely rather than present-and-empty. Join "
            f"against `opengloss-{release}-senses` if you need to know which senses have "
            "nothing."
        )
    if spec.slug in {"retrieval-triples", "qrels"}:
        extra = (
            "\n- **Pseudo-queries.** A sense with no written query falls back to its "
            "`grade_5/plain` gloss standing in as one; `query_source` says which happened, "
            "and a pseudo-query is a paraphrase of the document it is supposed to "
            "retrieve, which makes it easier than a real query. Filter on "
            "`query_source == 'generated'` for the harder set."
        )
    return f"""## Known limitations

- **It is synthetic.** Every string here was written by a language model against a
  schema, not transcribed from a corpus or checked by a lexicographer. It is
  well-formed and internally consistent; it is not attested usage, and it will contain
  confident errors. Do not use it as ground truth about what a word means.
- **Judge scores {JUDGE_SCORE}/100 (core + tier 2) and {JUDGE_SCORE_TIER3}/100 (tier 3).**
  A different model family (Claude Opus) scored fixed {JUDGE_SAMPLE_ENTRIES}-entry
  stratified samples at the close of each build. Sample statistics, not per-entry
  guarantees, and the judge is itself a model.
- **Relation precision is the weakest axis.** Relations were judged for validity and the
  ones that failed were demoted rather than asserted; symmetric reciprocity finished at
  {_pct(SYNONYM_RECIPROCITY)} for synonyms and {_pct(ANTONYM_RECIPROCITY)} for antonyms,
  and {_n(SENSES_WITHOUT_RELATIONS)} senses were left with no relation at all. Treat a
  single edge as a hypothesis, not a fact; treat the aggregate graph as usable.
{tier_note}
- **The encyclopedia is entry-level.** One article per *headword*, about the headword as
  a whole. On a polysemous entry it is not a description of any one sense, and it is
  never used as a positive for one (D-71). It is entry-level reference prose, not a
  specialist article.{extra}
"""


#: Repos whose card quotes the WordNet licence notice in full; every other card
#: references one of these two rather than repeating it (D-80).
_WORDNET_NOTICE_REPOS: tuple[str, ...] = ("lexicon", "senses")


def _sources_and_licences(spec: RepoSpec, stats: Stats, release: str) -> str:
    """Return the "Sources and licences" section every card carries (D-78, D-80).

    The release is CC-BY 4.0 throughout, but a `source` column on `lexicon` and
    `senses` flags the entries this project did not write: they are derived from
    Princeton WordNet 3.0 and carry its own licence. The notice text is quoted in full
    once, on the two repos that carry `source`, and referenced from every other card
    rather than repeated fifteen more times.

    Args:
        spec: The repo this card is for.
        stats: The export's statistics, for the WordNet-derived entry count.
        release: The release label, for the cross-link to `lexicon`.
    """
    wordnet_entries = stats.source_histogram.get(SOURCE_WORDNET, 0)
    count_line = (
        f"Of {_n(stats.lexemes)} lexemes in this release, **{_n(wordnet_entries)}** "
        f"(the tier-5 entries whose `source` column reads `{SOURCE_WORDNET}`) are "
        "derived from Princeton WordNet 3.0: their glosses, examples, relations and "
        "derivationally related forms, plus WordNet's own capitalisation of the "
        "headword (D-78)."
    )
    if spec.slug in _WORDNET_NOTICE_REPOS:
        notice = f"""> {WORDNET_LICENSE_NOTICE}

The full text is also reproduced in [`LICENSES/WordNet.txt`](\
https://github.com/mjbommar/opengloss-generator/blob/main/LICENSES/WordNet.txt) in the
source repository. Everything else in this dataset is original to OpenGloss and
licensed CC-BY 4.0 like the rest of the release."""
    else:
        notice = (
            f"The [WordNet License]({WORDNET_LICENSE_URL}) notice is quoted in full on "
            f"the `opengloss-{release}-lexicon` and `opengloss-{release}-senses` cards; "
            "this repo's WordNet-derived rows are governed by the same terms."
        )
    return f"""## Sources and licences

This release is **{LICENSE_NAME}**. {count_line}

The [WordNet License]({WORDNET_LICENSE_URL}) permits use, copying, modification and
distribution without fee, provided its notice is preserved:

{notice}
"""


def _citation() -> str:
    """Return the citation and license sections."""
    return f"""## Citation

```bibtex
@misc{{bommarito2025opengloss,
  title  = {{OpenGloss: A Synthetic Encyclopedic Dictionary and Semantic Knowledge Graph}},
  author = {{Bommarito, Michael J., II}},
  year   = {{2025}},
  eprint = {{2511.18622}},
  archivePrefix = {{arXiv}},
  url    = {{{PAPER_URL}}}
}}
```

Tier-5 entries additionally derive from Princeton WordNet 3.0 (D-78):

```bibtex
@article{{miller1995wordnet,
  title   = {{WordNet: A Lexical Database for English}},
  author  = {{Miller, George A.}},
  journal = {{Communications of the ACM}},
  volume  = {{38}},
  number  = {{11}},
  pages   = {{39--41}},
  year    = {{1995}}
}}

@book{{fellbaum1998wordnet,
  title     = {{WordNet: An Electronic Lexical Database}},
  editor    = {{Fellbaum, Christiane}},
  publisher = {{MIT Press}},
  year      = {{1998}}
}}
```

## License

Released under **{LICENSE_NAME}**. Attribution to the OpenGloss project is required;
commercial use is permitted. See [Sources and licences](#sources-and-licences) above for
the Princeton WordNet License that additionally covers this release's tier-5 entries.
"""


# --------------------------------------------------------------------------------------
# Per-repo statistics
# --------------------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float:
    """Return a safe ratio, zero when the denominator is zero.

    Args:
        numerator: The top.
        denominator: The bottom.
    """
    return numerator / denominator if denominator else 0.0


def _lexicon_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `lexicon` repo's own key statistics."""
    return [
        ("Entries with an etymology", _n(stats.etymologies)),
        ("Encyclopedia renditions", _n(stats.encyclopedia_renditions)),
        ("Contrast paragraphs", _n(stats.contrasts)),
        ("Recorded generation cost", f"${stats.provenance_cost_usd:,.2f}"),
    ]


def _senses_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `senses` repo's own key statistics."""
    return [
        ("Gloss renditions", _n(stats.gloss_renditions)),
        ("Example sentences", _n(stats.example_renditions)),
        ("Relations", _n(stats.relations_total)),
        ("Synthetic queries", _n(stats.queries)),
        ("QA pairs", _n(stats.qa_pairs)),
        ("Distinct domain leaves used", _n(len(stats.domain_leaves))),
    ]


def _definitions_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `definitions` repo's own key statistics."""
    per_sense = _ratio(stats.gloss_renditions, stats.live_senses)
    return [
        ("Renditions per sense (mean)", f"{per_sense:.1f}"),
        ("Distinct reading levels", _n(len(stats.reading_levels))),
        ("Distinct registers", _n(len(stats.registers))),
    ]


def _examples_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `examples` repo's own key statistics."""
    share = _ratio(stats.examples_with_span, stats.example_renditions)
    return [
        ("Sentences carrying a headword span", f"{_n(stats.examples_with_span)} ({share:.1%})"),
        ("From the per-sense examples stage", _n(stats.examples_by_source.get("per_sense", 0))),
        ("From rendition rewrites", _n(stats.examples_by_source.get("renditions", 0))),
    ]


def _encyclopedia_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `encyclopedia` repo's own key statistics."""
    mean = _ratio(stats.encyclopedia_words, stats.encyclopedia_renditions)
    return [
        ("Encyclopedia renditions", _n(stats.encyclopedia_renditions)),
        ("Lexical explanations", _n(stats.explanation_renditions)),
        ("Mean article length", f"{mean:.0f} words"),
    ]


def _etymology_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `etymology` repo's own key statistics."""
    mean = _ratio(stats.etymology_segments, stats.etymologies)
    return [
        ("Etymology segments", _n(stats.etymology_segments)),
        ("Segments per entry (mean)", f"{mean:.1f}"),
    ]


def _inflections_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `inflections` repo's own key statistics."""
    lemma_rows = stats.inflection_relations.get("lemma", 0)
    per_lexeme = _ratio(stats.inflection_forms, stats.lexemes)
    return [
        ("Forms", _n(stats.inflection_forms)),
        ("Lemma rows", _n(lemma_rows)),
        ("Rows per lexeme (mean)", f"{per_lexeme:.1f}"),
    ]


def _relations_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `relations` repo's own key statistics."""
    share = _ratio(stats.relations_resolved, stats.relations_total)
    return [
        ("Live edges", _n(stats.relations_total)),
        ("Resolved to a target sense", f"{_n(stats.relations_resolved)} ({share:.1%})"),
        ("Tombstoned edges recovered", _n(stats.tombstoned_relations)),
    ]


def _queries_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `queries` repo's own key statistics."""
    share = _ratio(stats.queries_headword_free, stats.queries)
    return [
        ("Queries", _n(stats.queries)),
        ("Headword-free", f"{_n(stats.queries_headword_free)} ({share:.1%})"),
        ("Query styles", _n(len(stats.query_styles))),
    ]


def _qa_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `qa-pairs` repo's own key statistics."""
    per_sense = _ratio(stats.qa_pairs, stats.live_senses)
    return [
        ("Pairs", _n(stats.qa_pairs)),
        ("Pairs per sense (mean)", f"{per_sense:.1f}"),
        ("Question types", _n(len(stats.question_types))),
    ]


def _contrasts_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `contrasts` repo's own key statistics."""
    return [("Contrast paragraphs", _n(stats.contrasts))]


def _provenance_stats(stats: Stats) -> list[tuple[str, str]]:
    """Return the `provenance` repo's own key statistics."""
    return [
        ("Recorded calls", _n(stats.provenance_records)),
        ("Total recorded cost", f"${stats.provenance_cost_usd:,.2f}"),
        ("Distinct models", _n(len(stats.provenance_models))),
        ("Distinct stages", _n(len(stats.provenance_stages))),
    ]


#: Repo slug -> the function that produces that repo's own "key statistics" rows. A repo
#: with no entry here is a derived one, and its rows come from the free exporter's own
#: summary instead (see :func:`_derived_stats`).
_STAT_ROWS: dict[str, Callable[[Stats], list[tuple[str, str]]]] = {
    "lexicon": _lexicon_stats,
    "senses": _senses_stats,
    "definitions": _definitions_stats,
    "examples": _examples_stats,
    "encyclopedia": _encyclopedia_stats,
    "etymology": _etymology_stats,
    "inflections": _inflections_stats,
    "relations": _relations_stats,
    "queries": _queries_stats,
    "qa-pairs": _qa_stats,
    "contrasts": _contrasts_stats,
    "provenance": _provenance_stats,
}


def _derived_stats(slug: str, stats: Stats) -> list[tuple[str, str]]:
    """Return key statistics for a repo derived from a free retrieval exporter.

    The exporter already reports what it did, in its own vocabulary (``by_negative_kind``,
    ``grade_histogram``, ``documents_by_template``), so the card shows that summary's
    scalar entries rather than a second, hand-maintained set of counters.

    Args:
        slug: The repo slug.
        stats: The export's statistics.
    """
    summary = stats.derived_summaries.get(slug, {})
    rows: list[tuple[str, str]] = []
    for key in sorted(summary):
        value = summary[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rows.append((f"`{key}`", _n(value)))
    return rows


def _repo_stat_rows(spec: RepoSpec, stats: Stats) -> list[tuple[str, str]]:
    """Return the repo-specific "key statistics" rows.

    Args:
        spec: The repo.
        stats: The export's statistics.
    """
    builder = _STAT_ROWS.get(spec.slug)
    if builder is None:
        return _derived_stats(spec.slug, stats)
    return builder(stats)


def _histograms(spec: RepoSpec, stats: Stats) -> str:
    """Return the repo's own distribution tables, if it has any worth showing.

    Args:
        spec: The repo.
        stats: The export's statistics.
    """
    blocks: list[str] = []
    if spec.slug in {"lexicon", "senses"}:
        blocks.append(
            "### Parts of speech\n\n"
            + _histogram_table(("POS", "Live senses"), stats.pos_histogram)
        )
        blocks.append(
            "### Domain roots\n\n"
            + _histogram_table(("Root", "Live senses"), stats.domain_root_histogram, limit=15)
        )
    if spec.slug == "senses":
        blocks.append(
            "### Relation types\n\n"
            + _histogram_table(("Type", "Edges"), stats.relation_type_histogram, limit=14)
        )
    if spec.slug == "definitions":
        blocks.append(
            "### Reading levels\n\n" + _histogram_table(("Level", "Rows"), stats.reading_levels)
        )
        blocks.append("### Registers\n\n" + _histogram_table(("Register", "Rows"), stats.registers))
    if spec.slug == "relations":
        blocks.append(
            "### Relation types\n\n"
            + _histogram_table(("Type", "Edges"), stats.relation_type_histogram, limit=14)
        )
        if stats.tombstoned_by_step:
            blocks.append(
                "### Tombstoned edges, by reconcile step\n\n"
                + _histogram_table(("Step", "Edges"), stats.tombstoned_by_step)
            )
    if spec.slug == "inflections":
        blocks.append(
            "### By relation\n\n"
            + _histogram_table(("Relation", "Rows"), stats.inflection_relations)
        )
    if spec.slug == "queries":
        blocks.append(
            "### Query styles\n\n" + _histogram_table(("Style", "Queries"), stats.query_styles)
        )
    if spec.slug == "qa-pairs":
        blocks.append(
            "### Question types\n\n" + _histogram_table(("Type", "Pairs"), stats.question_types)
        )
        blocks.append(
            "### Difficulty\n\n" + _histogram_table(("Difficulty", "Pairs"), stats.difficulties)
        )
    if spec.slug == "contrasts" and stats.contrast_verdicts:
        blocks.append(
            "### Verdicts\n\n"
            + _histogram_table(("Verdict", "Paragraphs"), stats.contrast_verdicts)
        )
    if spec.slug == "provenance":
        blocks.append(
            "### Calls by stage\n\n"
            + _histogram_table(("Stage", "Calls"), stats.provenance_stages, limit=20)
        )
        blocks.append(
            "### Calls by model\n\n"
            + _histogram_table(("Model", "Calls"), stats.provenance_models, limit=10)
        )
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------------------
# Loading snippets
# --------------------------------------------------------------------------------------


def _loading_section(spec: RepoSpec, owner: str, release: str) -> str:
    """Return the "loading it" section: `datasets`, then parquet directly.

    Args:
        spec: The repo.
        owner: The Hugging Face namespace.
        release: The release label this repo was published under.
    """
    repo_id = spec.repo_id(owner, release)
    if spec.single_config:
        load = f'ds = load_dataset("{repo_id}", split="train")'
        glob = f"hf://datasets/{repo_id}/data/train-*.parquet"
    else:
        first = spec.configs[0].name
        names = ", ".join(f'"{config.name}"' for config in spec.configs)
        load = f'# configs: {names}\nds = load_dataset("{repo_id}", "{first}", split="train")'
        glob = f"hf://datasets/{repo_id}/data/{first}/train-*.parquet"

    extra = ""
    if spec.extra_files:
        listed = "\n".join(f"- `{name}` — {desc}" for name, desc in spec.extra_files)
        extra = f"\n\nBeside the parquet shards this repo carries:\n\n{listed}\n"

    return f"""## Loading it

```python
from datasets import load_dataset

{load}
print(ds)
print(ds[0])
```

The shards are plain parquet, so nothing forces you through `datasets` — read them
straight, locally or over `hf://`:

```python
import polars as pl

df = pl.read_parquet("{glob}")
print(df.head())
```

```python
import duckdb

duckdb.sql("SELECT count(*) FROM '{glob}'").show()
```
{extra}
### {spec.snippet_title}

```python
{spec.snippet}
```
"""


# --------------------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------------------


def _title(spec: RepoSpec, release: str) -> str:
    """Return the card's H1.

    Args:
        spec: The repo.
        release: The release label this card is for.
    """
    pretty = spec.slug.replace("-", " ").title().replace("Qa", "QA").replace("Qrels", "Qrels")
    return f"# OpenGloss {release} — {pretty}"


def _fields_section(spec: RepoSpec, stats: Stats) -> str:
    """Return one fields table per config, with the config's row count and example row.

    Args:
        spec: The repo.
        stats: The export's statistics.
    """
    blocks: list[str] = []
    for config in spec.configs:
        rows = stats.rows.get((spec.slug, config.name), 0)
        table = _table(
            ("Field", "Type", "Description"),
            [(f"`{f.name}`", f"`{f.type_label}`", f.description) for f in config.fields],
        )
        heading = "" if spec.single_config else f"### Config `{config.name}`\n\n"
        blocks.append(
            f"{heading}"
            f"{_n(rows)} rows, {config.grain}.\n\n"
            f"{table}\n\n"
            f"**One real row:**\n\n"
            f"{_example_block(stats.example_rows.get((spec.slug, config.name)))}"
        )
    return "\n\n".join(blocks)


def _files_section(spec: RepoSpec, stats: Stats) -> str:
    """Return the shard inventory for this repo.

    Args:
        spec: The repo.
        stats: The export's statistics.
    """
    rows: list[tuple[str, ...]] = []
    for config in spec.configs:
        shards, size = stats.shards.get((spec.slug, config.name), (0, 0))
        rows.append(
            (
                f"`{spec.data_glob(config)}`",
                config.name,
                _n(stats.rows.get((spec.slug, config.name), 0)),
                _n(shards),
                _mb(size),
            )
        )
    return _table(("Files", "Config", "Rows", "Shards", "Size"), rows)


def _passes_note(stats: Stats) -> str:
    """Return the "how many frequency-ranked passes" sentence, counted from the data.

    Never a hard-coded "three": names whichever tiers this export actually contains
    (D-75).

    Args:
        stats: The export's statistics.
    """
    ranked = [tier for tier in stats.tiers_present if tier != TIER_UNKNOWN]
    if not ranked:
        return "This export's entries are not on any frequency-ranked list."
    names = _join_ticked(ranked)
    plural = "passes" if len(ranked) != 1 else "pass"
    return (
        f"The release was built in {len(ranked)} frequency-ranked {plural} ({names}) and "
        "they did not all receive the same stages."
    )


def render_card(
    spec: RepoSpec, stats: Stats, *, owner: str = DEFAULT_OWNER, release: str = DEFAULT_RELEASE
) -> str:
    """Render one repo's complete ``README.md``.

    Args:
        spec: The repo to document.
        stats: The statistics the export just produced. Every number in the card comes
            from here, so a card can never describe a different export than the one that
            wrote the shards beside it.
        owner: The Hugging Face namespace the family is published under, used for the
            cross-links in the family table and the loading snippets.
        release: The release label this export was built for (D-75). Every repo id, in
            the front matter, the family table and the loading snippets, is named for
            this rather than a literal, so ``--release vX`` reproduces the older
            release's naming exactly.

    Returns:
        The card, as markdown with YAML front matter.
    """
    key_stats = _table(
        ("", ""),
        [
            ("Lexemes", _n(stats.lexemes)),
            (
                "Retired lexemes (every sense tombstoned; not counted above)",
                _n(stats.retired_lexemes),
            ),
            ("Live senses", _n(stats.live_senses)),
            ("Rows in this dataset", _n(stats.rows_for(spec.slug))),
            *_repo_stat_rows(spec, stats),
        ],
    )
    histograms = _histograms(spec, stats)
    histogram_block = f"\n\n{histograms}" if histograms else ""

    card = f"""{_front_matter(spec, stats)}

{_title(spec, release)}

{spec.blurb}

Part of the **OpenGloss {release}** release family — {len(REPOS)} datasets built from one
store of {_n(stats.lexemes)} lexemes and {_n(stats.live_senses)} live senses, all joinable
on derived ids. See [Related datasets](#related-datasets) for the rest.

{_whats_new(stats, release)}
## Key statistics

{key_stats}

### By tier

{_release_stats(stats)}

### Coverage by tier

{_passes_note(stats)} This table is per-field and per-tier so the gaps are visible rather
than averaged away.

{_coverage_table(stats)}{histogram_block}

### Files

{_files_section(spec, stats)}

## Fields

{_fields_section(spec, stats)}

{_loading_section(spec, owner, release)}
{_ids_section()}
{_levels_section()}
## Related datasets

Everything below is built from the same store and joins on `lexeme_id` / `sense_id`.

{_family_table(spec, owner, release)}

{_limitations(spec, stats, release)}
{_sources_and_licences(spec, stats, release)}
{_citation()}"""
    # Every repo's blurb and code sample cross-references a sibling by name using the
    # placeholder release (PLACEHOLDER_RELEASE) rather than a literal, so this one
    # substitution is what makes ``--release vX`` reproduce the old repo names
    # everywhere they are mentioned in prose or in a code sample (D-75).
    card = card.replace(f"opengloss-{PLACEHOLDER_RELEASE}-", f"opengloss-{release}-")
    return card.replace(PLACEHOLDER_RELEASE, release)
