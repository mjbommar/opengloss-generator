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
from typing import TYPE_CHECKING, Any

from opengloss_generator.export.hf_rows import (
    COVERAGE_FEATURES,
    TIER_DESCRIPTIONS,
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

__all__ = ["V13", "render_card"]


# --------------------------------------------------------------------------------------
# Fixed facts about the release, and about v1.3, that no export can compute
# --------------------------------------------------------------------------------------


class V13:
    """Published v1.3 figures, for the honest scope comparison every card carries.

    Taken from the v1.3 definition-level dataset card, not re-derived: they describe a
    release this pipeline did not produce and cannot recount.
    """

    LEXEMES = 205_983
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
SYNONYM_RECIPROCITY = 0.980
ANTONYM_RECIPROCITY = 0.991

#: Senses left with no relation at all in that audit (of 137,314 live) — the largest
#: known gap.
SENSES_WITHOUT_RELATIONS = 3_709

PAPER_URL = "https://arxiv.org/abs/2511.18622"
LICENSE_ID = "cc-by-4.0"
LICENSE_NAME = "Creative Commons Attribution 4.0 International (CC-BY 4.0)"

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


def _whats_new(stats: Stats) -> str:
    """Return the "what's new in v2.0" section, with the honest scope note.

    Args:
        stats: The export's statistics.
    """
    scope = _table(
        ("", "v1.3", "v2.0"),
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
    return f"""## What's new in v2.0 vs v1.3

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
   senses (`3d_model_noun_0`) and nothing below that. v2.0 gives every rendition, edge,
   query, QA pair and provenance record an id computable from the row alone, and never
   renumbers: a retired sense is tombstoned, so the ids after it keep their meaning.
6. **Per-field provenance.** Which model wrote a field, how many tokens it took, what it
   cost — published as its own dataset.

### Scope: fewer headwords, far more per headword

v2.0 is **not** a superset of v1.3. It covers {_n(stats.lexemes)} lexemes — a
frequency-ranked subset of v1.3's {_n(V13.LEXEMES)} — and spends the difference on depth.
If you need breadth of vocabulary, use
[v1.3]({V13.URL}); if you need graded renditions, resolved
relations, spans, or retrieval supervision, use v2.0.

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

## License

Released under **{LICENSE_NAME}**. Attribution to the OpenGloss project is required;
commercial use is permitted.
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
            this rather than a literal, so ``--release v2.0`` reproduces the older
            release's naming exactly.

    Returns:
        The card, as markdown with YAML front matter.
    """
    key_stats = _table(
        ("", ""),
        [
            ("Lexemes", _n(stats.lexemes)),
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

{_whats_new(stats)}
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
{_citation()}"""
    # Every repo's blurb and code sample cross-references a sibling by name using the
    # placeholder release (PLACEHOLDER_RELEASE) rather than a literal, so this one
    # substitution is what makes ``--release v2.0`` reproduce the old repo names
    # everywhere they are mentioned in prose or in a code sample (D-75).
    return card.replace(f"opengloss-{PLACEHOLDER_RELEASE}-", f"opengloss-{release}-")
