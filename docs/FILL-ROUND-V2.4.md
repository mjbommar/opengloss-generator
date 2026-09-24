# Fill round toward v2.4 — gpt-6-luna, flex, reasoning low

**Started:** 2026-09-24 03:24 UTC  
**Goal (user):** fill in as much OpenGloss content as possible with `gpt-6-luna`,
`service_tier=flex` and `reasoning=low`, carefully and consistently, until at least a few
hundred million tokens have been generated. The content serves two purposes: a better
ontology for lookup and reference, and anchoring data for micro vector/embedding models.  
**Evidence base:** [CENSUS-V2.3.md](CENSUS-V2.3.md) (what v2.3 lacks) and
`reports/writer-probe-2026-09-23/` (gpt-6-luna is half the price of gpt-5.6-luna, with equal
or better blinded quality).

## Boundary

- v2.3 on the Hub is immutable. This round writes into the working store `data/core-store`,
  which is the store a future release will be exported from.
- **Rollback point:** `/data1/opengloss-generator/snapshots/core-store-v2.3-20260923.tar.zst`
  (1.22 GB, zstd tar of the store as released).
- Every call goes through the existing stages (`StageRunner`), so provenance records the
  model, tier, tokens and cost of each call. Run logs and ledgers are in `runs/`.
- The profile is `scripts/gpt6-luna.toml`. Every generating stage uses `gpt-6-luna`, flex,
  reasoning `low`, with no writer rotation. `max_flex_429s` is set high so the run never
  silently downgrades to `auto`.
- `pricing.py` gains `gpt-6-luna` ($0.10 / $0.01 / $0.50 standard) and `gpt-6-sol` rows,
  verified on the pricing page on 2026-09-23. Flex is priced at the batch rate.

## Why these stages (census findings they close)

| stage | scope | census gap |
| --- | --- | --- |
| `queries` | tiers 3–6, 196,637 senses | 65% of senses had no query |
| `qa-pairs` | tiers 3–6 | same senses had no QA |
| `enrich --fields gloss --registers informal,formal,technical,marketing` | tiers 3–6 | register variants existed only for core/tier 2; register was perfectly predictable from tier |
| `enrich --fields examples --registers informal,formal,technical,slang` | tiers 3–6 | register examples existed only for tier 2 |
| `contrasts` | tiers 3–6 | contrasts existed only for core/tier 2 |
| `enrich --fields encyclopedia --reading-levels grade_1,grade_10` | tiers 3–6 | only 9,427 lexemes had these levels |

For **lookup/reference**, every entry gains the same kinds of section that core/tier-2 entries
already have: register glosses, QA, contrasts and graded encyclopedia text. For **embedding
anchoring**, every sense gains natural-language queries (mostly headword-free), grounded QA,
and register-crossed paraphrases of the same meaning.

## Execution

- Tiers 3–6 live lexemes (120,951) are split into twelve disjoint shards by SHA-256 of the
  lexeme id mod 12 (`/data1/opengloss-generator/fill-v2.4/lists/t36.sNN.txt`).
- One systemd user service per shard (`opengloss-fill-s{00..11}`) runs
  `/data1/opengloss-generator/fill-v2.4/chain.sh`. The stages run **sequentially within a
  shard**, because a stage holds an entry's lock across its model calls and a lock over 900 s
  can be broken as stale. The shards are disjoint, so the four processes never contend.
- Each process has 256 workers and a twelfth of the account's gpt-6-luna limits (30,000 RPM
  and 180M TPM, read from response headers on 2026-09-23). Flex calls take roughly 5–30 s.
- Every stage is idempotent. Rerunning `chain.sh K` resumes at about $0 for finished work.
- Per-stage budget caps per shard are $3.5 / $5.5 / $2.5 / $2.5 / $2.0 / $4.5, a ceiling of
  $246 over the twelve shards.
- Progress comes from `python3 /data1/opengloss-generator/fill-v2.4/progress.py`, which sums
  every `stage_complete` event since launch by stage × model × tier.

## Pre-launch acceptance (2026-09-24 03:10–03:24 UTC)

- **Smoke store.** Ten entries (tiers 3–6 plus `bank` and `argue`) copied to a scratch store.
  Every stage ran on gpt-6-luna flex with 0 failures and 0 downgrades:

  | stage | calls | cost | output tokens | result |
  | --- | ---: | ---: | ---: | --- |
  | `queries` | 11 | $0.0014 | 4,477 | 132 stored, 87% headword-free, all 8 styles |
  | `qa-pairs` | 11 | $0.0025 | 7,297 | |
  | gloss registers | 12 | $0.0012 | 3,024 | |
  | example registers | 24 | $0.0017 | 4,599 | |
  | encyclopedia levels | 12 | $0.0032 | 8,670 | |

  `contrasts` made no calls because the smoke store lacks the pair targets. The dry run found
  31,371 contrasts due in shard 0.
- **Dry run, shard 0.** 49,010 query calls, 49,010 QA calls, 49,010 gloss-register calls,
  48,561 example-register calls, 13,953 contrast calls and 30,137 encyclopedia calls.
- **Projection.** About 1M calls, about $155 and about 400M output tokens over tiers 3–6.

## Log

- 03:24 UTC: four shard chains launched on the `queries` stage.
- 03:24–03:36 UTC: the first launch used four shards and exposed two issues. (1) The
  chain's summary line ran `json.load` on an empty summary, which was cosmetic. (2) A signal
  to the service fell through to the next stage. The four services were stopped, and
  `chain.sh` now traps `INT`/`TERM`, forwards `INT` to the running stage, waits for its
  graceful stop and exits. Relaunched as twelve shards at 03:36. Every stage resumed at $0
  for finished senses.
- 03:40 UTC: throughput was about 27 calls/s against the ~130/s that 2,304 workers at ~17 s
  latency allow. Every process had **exactly 100 open sockets**: httpx's default pool limit,
  inherited through pydantic-ai's `infer_model`. `router._build_openai_model` now gives
  OpenAI models a client whose pool is `2 × workers`, with a regression test in
  `tests/test_writers.py`; the full suite passes. The services were stopped cleanly (every
  stage logged `stop_reason=stopped` and no locks were left) and relaunched at 03:46 with
  256 workers each. Each process now holds 256 sockets.
- 04:45 UTC: **309.6M output tokens, $99.04, 758K calls**, all on gpt-6-luna flex. From about
  04:20, flex capacity for gpt-6-luna tightened: 52K "currently processing too many requests"
  429s and 7K connection errors, with 8.4K items using up their three attempts. Rejections are
  unbilled, and the phase-2 remainder sweep redoes every missed item at ~$0 for finished work.
  `max_attempts` was raised to 6 for stages launched after this point. Throughput fell from
  ~280 to ~100 calls/s. The tier is never downgraded.
- Quality checks so far:
  - **queries**: 1.94M stored over 161K senses, 87.2% headword-free (v2.3: 77.8%), all 8
    styles for 99.8% of senses, and 623 rejected.
  - **qa-pairs**: 976K accepted of 1.007M generated (97.0%), with 555 empty sets.
  - **Register glosses**: a sample of tier-3 entries shows distinct registers and faithful
    meanings, and none opens with the headword.
- 05:46 UTC: **phase 1 complete on all twelve shards.** Every stage reported
  `stop_reason=completed` within its cap, except shard 10's `queries` stage, which segfaulted
  7 s after start (exit 139, core dumped, in the interpreter). The phase-2 remainder sweep
  covers it. Running total: **507.7M output tokens, $167.37, 1.11M calls.**

## Phases 2–4 (queued as systemd user services, each waiting on the previous phase)

- **Phase 2** (`phase2.sh`, `opengloss-fill-p2s{00..11}`) fills the core and tier-2 gaps:
  query and QA remainders, contrasts, example registers (core had none), and encyclopedia
  grade 1/10 (tier 2 had none). It then sweeps every phase-1 stage over the shard's tiers 3–6
  list again, which costs ~$0 for finished work and redoes flex-rejected items.
- **Phase 3** (`phase3.sh`, `opengloss-fill-p3s{00..11}`) runs `examples` (D-53: eight
  sense-disambiguated, deterministically verified sentences per live sense) for core and
  tiers 3–6. Only tier 2 had them in v2.3. A dry run estimated about 1.58M sentences for
  tiers 3–6 plus 257K for core. These are the best word-in-context anchors the framework
  produces.
- **Phase 4** (`phase4.sh`, `opengloss-fill-p4`) runs store-wide once all of phase 3 is done:
  `relation-regen` (3,736 relationless senses), `resolve --all`, `graph-hygiene`,
  `relation-hygiene`, and then `audit`. This is the v2.3 closing order.
- 06:31–07:17 UTC: phases 2 and 3 complete on all shards; every stage reported `completed`.
  Shard 10's `r_queries` stopped at its $1 cap, since that shard's phase-1 queries had crashed;
  a follow-up run completed it (6,577 calls, $0.78).
- 07:47 UTC: **phase 4 complete.**
  - `relation-regen`: $0.41.
  - `resolve --all`: $0.61.
  - `graph-hygiene`: deterministic.
  - `relation-hygiene` validity: 165K entries, 47K changed, $8.37.
  - `audit`: found **731 hypernym cycles**. v2.3 closed with 0; relations regenerated and
    resolved late in the round had reintroduced them, and `graph-hygiene` had run before
    `relation-hygiene`. A second `graph-hygiene` pass broke them deterministically: 67
    cycle edges demoted, 527 mutual demotions, and 9,527 reciprocal edges added. The re-audit
    shows **0 cycles and 0 self-loops**. Senses with zero relations fell from 4,722 (v2.3) to
    **1,643**, and resolved relations total 1,111,427 (64.7%). **Lesson for the next chain:**
    run `graph-hygiene` last, after `relation-hygiene`.
- 08:03 UTC: **phase 5 (remainder sweep) complete.** 167 of 168 stages completed. Shard 08's
  `contrasts` segfaulted (exit 139, the same interpreter crash seen once in phase 1); it was
  rerun at 08:04 and completed (360 calls, $0.03).

## Totals

`progress.py` at the end of generation: TOTAL calls=1,470,015 in= 3,921,125,406 cached= 3,188,892,084 out= 710,809,633 $ 230.26 attempt_failures=77689 downgrades=0 http429=318934

Content added to the working store:

| content | added |
| --- | ---: |
| search queries (87% headword-free) | 2,356,750 |
| QA pairs (97.0% of generated accepted) | 1,491,029 |
| gloss register renditions (tiers 3–6) | 786,548 |
| example register renditions | 942,295 |
| contrast paragraphs | 177,259 |
| encyclopedia renditions (grade 1 / grade 10) | 302,594 |
| verified sense-tagged example sentences (D-53) | 1,575,783 |

## Verification: local v2.4-rc export and census (no push)

`export-hf --release v2.4-rc --out /data1/opengloss-generator/v2.4-rc/hf` finished cleanly in
27 min with 3.3 GB peak memory (16 repos, 6.1 GB). `scripts/census_v23.py --release v2.4-rc`
wrote `reports/census-v2.4-rc/census.json`. Comparison with `reports/census-v2.3/census.json`:

| measure | v2.3 | v2.4-rc |
| --- | ---: | ---: |
| live senses with no query | 65.4% | **0.0%** |
| live senses with no QA pair | 65.5% | **0.3%** |
| senses with no example | 0.65% | 0.18% |
| senses with no relation | 1.57% | 0.55% |
| register variant present, tiers 3–6 | 0% | **100%** |
| register ↔ tier mutual information (normalized) | 1.0 | **0.0** |
| per-sense training-exposure Gini | 0.40 | **0.15** |
| queries / QA pairs / contrasts (rows) | 1.25M / 0.70M / 81K | 3.85M / 2.30M / 271K |
| unique source-text tokens (excluding pretrain) | 697.6M | **1,245.3M** |
| listwise lists with more than one grade-3 positive | 13.9% | 27.6% |

Diversity held or improved: distinct-4-gram rates rose for queries (0.894 → 0.921), QA
questions (0.928 → 0.944) and register glosses (0.970 → 0.984). Only 180 of the 3.27M queries
flagged headword-free contain the headword (0.006%).

Open items for a real v2.4 release, all deterministic:

- The pretrain level fan-out duplicates (errata in [CENSUS-V2.3.md](CENSUS-V2.3.md)) now
  reach 32.9% of pretrain documents, because the new content is fanned out the same way. Fix
  the exporter before any release.
- QA-answer exact duplicates rose from 0.33% to 1.14% (short identical answers). Deduplicate
  within a sense at export.
- The hypernym cycles that phase 4 reintroduced were repaired, and the audit is at 0; keep
  `graph-hygiene` last in future chains.
- Two interpreter segfaults (exit 139) happened in 36 × 6 stage launches. Both reruns were
  clean, so there is no data effect, but the cause (Python 3.14 plus a native extension) is
  still unknown.
