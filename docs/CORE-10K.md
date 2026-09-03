# The core lexicon (10K)

Date: 2026-09-02. Script: `scripts/core_lexicon.py`. Output: `data/core/core_10k.tsv`.
Zero model calls.

## Method

Every v1.3 entry is scored by the **mean percentile rank of four signals**, two internal
and two external, so no single source's bias dominates:

| Signal | Source | What it measures |
|---|---|---|
| `wiki_frequency` | in the entry | how often the word is *used* in Wikipedia |
| `in_degree` | relation targets across the whole store | how many other entries' definitions *depend on* the word |
| `subs_rank` | OpenSubtitles en_50k | spoken frequency |
| `g10k_rank` | Google web 10K | web frequency |

Missing external ranks count as worst, so a word must have broad support to rank high.

Candidates must be: not a stopword, a single token (`^[a-z][a-z-]{1,14}$`), and attested
in at least one independent list (wamerican, Google 10K, OpenSubtitles 50K, popular-25K).
Entries with in-degree > 500 but Wikipedia frequency < 1,000 and no web rank are dropped:
those are hypernym-slot artifacts (`descriptor`, `descriptive term`, `descriptive
adjective`), which otherwise top the in-degree ranking.

## Result

Funnel: 205,988 entries → 129,239 single-token non-stopwords → 75,793 with external
attestation → 75,785 after the artifact guard → **top 10,000**.

- Covers **90.7%** of Google's top 5,000 and 4,182 of OpenSubtitles' top 5,000; the
  misses are web junk (`nov`, `rss`, `faq`, `pdf`, `paypal`) that we *want* excluded.
- 9,902 / 10,000 are in wamerican; all 10,000 have an encyclopedia entry.
- Mean 3.9 senses per word (store average 2.75) and mean in-degree 106 — the core is
  the polysemous, heavily-referenced part of the graph, as it should be.
- POS (multi-count): noun 7,770 · verb 4,285 · adjective 3,161 · adverb 420.
- Rank samples: #1 `people`, #2 `name`, #3 `work`, #10 `open`, #100 `stop`, #500 `round`,
  #1000 `organized`, #5000 `handled`, #9000 `eager`, #9999 `vow`. Just outside: `yell`,
  `plaque`, `constellation`, `eruption`.

## Two findings worth acting on

1. **v1.3 is missing basic function words.** 100 of Google's top 3,000 are absent from
   the store entirely — mostly single letters, but also `of`, `if`, `he`, `up`, `so`,
   `us`. The v1.0 lexeme selection applied a 3–15 character filter to wamerican, which
   excluded every two-letter word. A `function_word` pass over a closed-class list would
   close this for a few dollars.
2. **The core contains inflected forms as separate entries** (`organized`, `handled`,
   `builds`, `postponed`), because v1.3 did. A lemma-level core would fold each onto its
   base form using the `Morphology` block; that is the natural next refinement and needs
   one more column (`all_inflections`) from the shards.

## What the core is for

It is the subset where the expensive enrichments pay off first: encyclopedia renditions
(`docs/COST-MODEL.md` — the dominant cost), full `resolve`, and QA sampling. A rendition
on `people` reaches more learners and more edges than one on `vow`.
