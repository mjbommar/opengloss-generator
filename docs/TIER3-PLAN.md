# Tier 3 plan — the rest of the frequency-ranked lexicon (2026-09-04)

Status of the store when this plan was written: 41,886 entries enriched to schema v3
(core 10K + tier 2), judge 70.2 on the fixed 40-entry sample, every field at full
coverage, exports on disk (`data/exports/`), `main` pushed. Goal-2 spend $114; total
enrichment spend to date ≈ $430. See `docs/CORE-DIARY.md` for the stage-by-stage record.

## 1. What is left, and why "top 100K" is not a tier

The ranking in `scripts/core_lexicon.py` scores single-token words by the mean
percentile of Wikipedia frequency, graph in-degree, OpenSubtitles rank, and Google-10K
rank. Only 75,785 of the 205,988 v1.3 lexemes qualify for it.

| group | entries | v1.3 senses | median wiki freq | state |
|---|---|---|---|---|
| core 10K | 10,000 | 39K | 47,720 | done |
| tier 2 | 31,886 | 102K | 3,590 | done |
| **tier 3 — rest of ranked** | **33,899** | **109K** | 209 | this plan |
| unranked, multiword | 74,073 | ~185K | none | separate cut (§ 6) |
| unranked, no frequency signal | 55,643 | ~140K | none | separate cut (§ 6) |
| stopwords | 603 | | | skip |

Tier 3's list is `data/core/tier3_rest.tsv` (rank, word, wiki_frequency, in_degree,
total_senses), in ranking order. Its median in-degree is 0: almost nothing else in the
graph points at these words, so `resolve` will find fewer targets and relation lists
will be thinner than tier 2's. That is expected and does not affect the text fields.

The list carries inflected forms that v1.3 stored as their own lexemes (`evolved`,
`spoiled`, `pursuing`, `dispersed`). Step 0 below folds those whose lemma is already
enriched; the count after folding is the real tier size.

## 2. Measured unit costs (tier 2, 31,886 entries, 76,855 live senses)

| field / pass | $ per entry | pretraining words per entry | words per $ |
|---|---|---|---|
| structural: classify_kind, hygiene, tag_domain (luna), spans, repair, resolve, graph-hygiene, relation validity, sense-hygiene | 0.0022 | 0 | prerequisite |
| encyclopedia at grade_5 + college | 0.0015 | ~700 | 470K |
| gloss × 4 registers | 0.0005 | 170 | 340K |
| example × 4 reading levels | 0.0006 | 180 | 290K |
| gloss × 4 reading levels | 0.0009 | 160 | 180K |
| per-sense examples (8, sense-checked) | 0.0008 | 100 | 125K |
| rewrites: filler, circular gloss, readability, rendition hygiene | 0.0003 | 0 | quality only |
| queries + contrasts + QA pairs | 0.0020 | ~600 (as pairs) | value unproven |
| judge sample (40 entries, Opus) | $3.40 per sample | | instrument |

Every figure above is a ledger total divided by the tier-2 entry count; none is a
projection. Tier 3 has 3.2 v1.3 senses per entry vs tier 2's 3.2, so per-entry costs
should transfer; sense-hygiene retired ~25% of tier 2's senses, so expect ~80K live
senses.

## 3. Recipes

| recipe | fields | est. cost for 33,899 entries |
|---|---|---|
| **A. text-only (recommended start)** | structural + encyclopedia levels + gloss levels + example levels + one judge sample | **~$175** |
| B. full renditions | A + gloss registers + per-sense examples | ~$220 |
| C. everything tier 2 got | B + rewrites + the three pair stages + judges | ~$340 |

Recipe A carries the fields with the best words-per-dollar and the structural passes
that make the relations trustworthy. The pair stages wait for the encoder experiment
on the 42K exports (`docs/RETRIEVAL-DATA.md`) to show they earn their keep; the rewrite
passes wait for the judge sample to say what tier 3 actually gets wrong. Every stage is
idempotent, so B and C can be added later without redoing A.

## 4. Recipe A, stage by stage

Caps are 1.5× the tier-2 unit cost × 33,899. One luna process at a time, ≤ 48 workers
(the 429 rule); nano stages may run at 64. Each stage logs to `docs/CORE-DIARY.md`.

| # | stage | model | cap | expected |
|---|---|---|---|---|
| 0 | migrate the 33,899 from the v1.3 source (`/nas4/data/workspace/curriculum/data/lexicon`) into `data/core-store`; fold inflected-form entries whose lemma is enriched | — | $0 | free, ~1 h |
| 1 | `retrofit --only all` (classify_kind, hygiene, tag_domain, spans, repair, readability, rendition) | nano + luna tag | $8 | $5 |
| 2 | `resolve --all` then `graph-hygiene` | nano | $20 | $15 |
| 3 | `enrich --fields gloss --reading-levels grade_1,grade_5,grade_10,college` | luna | $45 | $30 |
| 4 | `enrich --fields examples --reading-levels …` | luna | $32 | $21 |
| 5 | `enrich --fields encyclopedia --reading-levels grade_5,college` | luna | $75 | $50 |
| 6 | `content-hygiene`, `relation-hygiene` (validity), `sense-hygiene`, `repair`, example catch-up, `graph-hygiene` | nano | $60 | $40 |
| 7 | `relation-reconcile` ×2, `graph-hygiene` | — | $0 | free |
| 8 | `qa --sample 40 --seed 7` on tier 3 (own sample), `audit` | Opus | $5 | $3.40 |
| | **total** | | **$245 of caps** | **~$165–175** |

Known chain gotchas, all hit on tier 2: `retrofit --only` takes one pass name (the
chain's comma list failed silently); `--force-retag-domains` lives in the `hygiene`
pass, not `tag_domain`; the flex tier may downgrade to `auto` under capacity pressure
(doubles unit cost, halves time; caps still hold); pooled sweeps write no per-item
ledger, their cost is in the printed summary and in provenance; markers are only
reliable on `main` after the provenance-order fix (bbf96d3).

Wall-clock at tier-2 rates: stages 3–5 ≈ 6 h, structural + hygiene ≈ 4 h → about 10
hours, one calendar day with the follow-ups.

## 5. Decision points

1. **Before stage 1:** approve the recipe-A budget (~$175, caps $245).
2. **After stage 8:** read the judge sample. If tier 3 scores within ~3 points of tier 2
   (70.2), proceed to recipe B for $45; if not, look at the defect mix first — the
   rewrite passes cost $10 and the diary says which pass targets which criterion.
3. **After the encoder experiment:** if the retrieval pairs move a downstream number,
   run the three pair stages on tier 3 ($67) and revisit the 80/20 writer rotation for
   them (measured 4× cost at 20% haiku; `docs/WRITER-DIVERSITY.md` "Cost correction").

## 6. The unranked 130K (not in this plan)

The multiword entries (74K: `composite number`, `national symbol`, `genome analysis`)
are dictionary-worthy and probably the more valuable half; the no-frequency entries
(56K: `fechner`, `swapo party`, `hanoverians`) are mostly names and Wikidata gap-fill.
Neither can be ranked by word frequency. A cut for them needs a different signal —
in-degree from the enriched 42K, whether v1.3 gave them an encyclopedia entry, and the
`kind` classifier (free) to separate compounds from proper nouns. Decide after tier 3.

## 7. What this plan does not do

No writer rotation on the text stages (luna only; the rotation stays configured on
RENDITIONS/EXAMPLES policies but tier 3 runs with the luna-only override until the
cost question is settled). No pair stages. No HF export — that is the v2.0 release
step in the paper plan and should wait until the 42K + tier 3 are judged together.
