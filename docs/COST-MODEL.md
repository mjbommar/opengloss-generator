# OpenGloss Generator — Cost model

Companion to `DESIGN.md` § 4 (cost architecture) and § 5 (stages and workflows), and to
`docs/SCHEMA-V3.md` § 5, which is the contract this document works the arithmetic for.
Date: 2026-09-02.

Every number below is a worked *estimate* under the stated assumptions, not a measured
result — nothing in this repository has run against a live model yet. The point is the
arithmetic and the shape of the answer (which stage dominates, and why), not the fourth
significant figure. § 5 lists what to actually measure once a run exists.

## 1. Assumptions

Given by the brief:

- 3 senses per entry.
- Instructions block ~600 tokens, cached after the first call in a run.
- Taxonomy prompt block (`TAXONOMY_PROMPT_BLOCK`) ~1,500 tokens, cached.
- One gloss ~30 tokens; one example ~20 tokens; one canonical encyclopedia entry ~500
  tokens.
- 12 relations per entry; 50% of relation targets already exist in the store.
- Deterministic `kind` resolution rate: 85% (15% residue reaches the model).
- Deterministic span resolution rate: 90% (10% of examples reach the fallback).
- `gpt-5.4-nano` flex: $0.10 / $0.01 / $0.625 per 1M tokens (in / cached / out).
- `gpt-5.6-luna` flex: $0.10 / $0.01 / $0.60 per 1M tokens (in / cached / out).

Additional assumptions this document introduces, because the stage table in
`SCHEMA-V3.md` §5 does not size every input and output (flagged here so a reader can
substitute real numbers once they exist):

- One example per sense (3 examples/entry), not stated in the brief.
- Output-token sizes for structured fields not given a token count: a `kind`
  classification ~4 tokens/term; a domain-tag decision ~10 tokens/sense; a resolved
  target (`sense_id` + `confidence`) ~8 tokens/target; a found span ~6 tokens/example; a
  headword's `overview` output (POS list, sense count, `kind`, per-sense `domain`) ~100
  tokens; a `senses`-stage call's combined output (3 glosses, 3 examples, 12 relations,
  confusables) ~500 tokens; an `etymology` output ~150 tokens; a `lexical_explanation`
  output ~80 tokens.
- One `overview` and one `senses` call per entry (not per POS entry), to keep the
  arithmetic to one line per stage; a real entry with multiple parts of speech pays
  proportionally more for `senses`.
- The prompt cache is warm for every call after the first *within a stage*, per run —
  i.e. the run is large enough, and `openai_prompt_cache_key` consistent enough
  (`DESIGN.md` § 4.3), that only the first call of each stage pays the fresh-instruction
  price. A short or fragmented run will do worse than these numbers.

## 2. Per-stage cost model

The rule from `SCHEMA-V3.md` §5: the context window carries only what the decision
needs; static content goes in instructions so it is cached; effort is `low` unless the
stage is writing prose.

### `classify_kind` — nano, low, 50 terms/call

**Context.** The bare residue terms only — headwords the deterministic rules in
`migrate.classify_kind_deterministic` could not place. Nothing else: the decision is a
one-of-eight enum choice per term, so sending a gloss or example would be pure waste.

**Static/volatile.** Instructions (~600 tok) are the only static content; cached from
the second call on. The batch of terms is volatile every call.

**Model/effort.** `gpt-5.4-nano`, `low` — an eight-way classification, not prose.

**Batching.** 50 terms/call.

**Arithmetic** (1,000 entries, 85% resolved deterministically → 150 headwords reach the
model → ⌈150/50⌉ = 3 calls, each ~50 terms × ~2 tok/term = 100 tok fresh, ~4 tok/term
output = 200 tok):

| | tokens | rate ($/MTok) | cost |
|---|---|---|---|
| fresh input (1×700 + 2×100) | 900 | 0.10 | $0.00009 |
| cached input (2×600) | 1,200 | 0.01 | $0.000012 |
| output (3×200) | 600 | 0.625 | $0.000375 |
| **Total / 1,000 entries** | | | **≈ $0.0005** |

Negligible — as expected, since 85% of the work costs nothing.

### `tag_domain` — nano, low, 1 call/entry (all senses)

**Context.** Headword + every sense's canonical gloss (3 × 30 tok = 90 tok, matching the
brief's "100–300 tokens" for headword + glosses). The full leaf list lives in
*instructions* (taxonomy block, 1,500 tok, cached), not in the call.

**Static/volatile.** Instructions + taxonomy block (600 + 1,500 = 2,100 tok) cached
after the first call; headword + glosses volatile every call.

**Model/effort.** `gpt-5.4-nano`, `low` — structured output constrains the answer to the
enum, so there is no free text to reason about.

**Batching.** 1 call per entry, covering every sense.

**Arithmetic** (1,000 calls; ~100 tok fresh input; ~10 tok output/sense × 3 = 30 tok
output):

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (2,200 + 999×100) | 102,100 | 0.10 | $0.01021 |
| cached input (999×2,100) | 2,097,900 | 0.01 | $0.02098 |
| output (1,000×30) | 30,000 | 0.625 | $0.01875 |
| **Total / 1,000 entries** | | | **≈ $0.050** |

### `resolve` — nano, none, 1 call/entry, ≤40 targets/call

**Context.** The source sense's canonical gloss, plus `(index, canonical gloss)` for
each *unresolved* target that exists in the store. 12 relations/entry, 50% present in
store → 6 targets/entry actually sent; the other 6 never reach the model.

**Static/volatile.** Instructions (~1,624 tok, `RESOLVE_INSTRUCTIONS`) cached from the
second call on; gloss + candidate list volatile.

**Model/effort.** `gpt-5.4-nano`, `reasoning_effort="none"` — picking a sense id from a
short numbered list and stating a confidence, not writing prose, so reasoning is turned
off rather than merely turned down to `"low"` (§ "Measured live" below explains why this
line changed from `"low"`).

**Batching.** 1 call/entry (well under the 40-target cap at 6 targets/entry).

**Arithmetic** (1,000 calls; source gloss 30 tok + 6×30 tok candidate glosses + ~12 tok
overhead ≈ 222 tok fresh input; output 6×8 = 48 tok):

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (822 + 999×222) | 222,600 | 0.10 | $0.02226 |
| cached input (999×600) | 599,400 | 0.01 | $0.00599 |
| output (1,000×48) | 48,000 | 0.625 | $0.03000 |
| **Total / 1,000 entries** | | | **≈ $0.058** |

The other 6 targets/entry (absent from the store) cost exactly $0 — they are filtered
out before the call is built, per `SCHEMA-V3.md` §5's note that this is "at zero cost".

#### Measured live (2026-09-01), and the two fixes it drove (D-38)

A live `resolve_store` run against real data (`gpt-5.4-nano`, flex, the `"low"`-effort,
~280-token-instructions policy that predates this section) measured, over 7,474 calls:

| Metric | Measured |
|---|---|
| `cache_hit_rate` | 0.0015 |
| input tokens (total) | 18.8M → **≈2,515 tok/call** |
| output tokens (total) | 4.9M → **≈655 tok/call** |
| cost | **$0.00066/call** — 2.5× the cost model's per-call estimate for this stage |

Two causes, both visible in the numbers above rather than assumed:

1. **`RESOLVE_INSTRUCTIONS` was ~279 tokens (1,117 chars).** OpenAI's prompt cache only
   matches a prefix of 1,024 tokens or more (the same finding `RENDITIONS_INSTRUCTIONS`
   documents in `config.py` and `docs/CORE-DIARY.md` Iteration 2 finding 3). Below that
   floor, nothing is ever cached regardless of call volume or a consistent
   `openai_prompt_cache_key` — hence `cache_hit_rate` of 0.0015 (noise) against
   `tag_domain`'s comparable in-run hit rate in the 80%+ range once its 2,100-token
   cached prefix is warm. Fix: `RESOLVE_INSTRUCTIONS` is now 7,341 characters (≈1,624
   tokens by `tiktoken`), comfortably over the floor, static, and byte-stable, so it
   caches like every other stage's instructions from the second call in a run onward.
2. **~655 output tokens/call against a 3-field contract (`target_ref`, `sense_choice`,
   `confidence`).** `max_tokens` on the Responses API includes reasoning tokens
   (`config.py`'s own note on prose stages), and `reasoning_effort="low"` was leaving
   the model free to reason at length before emitting the three numbers the schema asks
   for — for a batch of ~6 targets that is roughly 100+ hidden reasoning tokens per
   target, dwarfing the visible answer. Fix, two parts: `reasoning_effort` moved to
   `"none"` (`gpt-5.4-nano` supports `openai_supports_reasoning_effort_none`, so this
   disables reasoning outright rather than reducing it), and `RESOLVE_INSTRUCTIONS`'s
   final section states explicitly that the output carries only the choice and the
   confidence, no restated gloss and no rationale — the worked example's "Reasoning"
   annotations are marked as not part of the answer format for exactly this reason.

**Expected effect, worked at the measured 2,515 tok/call input split** (2,236 tok/call
volatile content — the part of the old 2,515 total that was not the 279-token
instructions — is unaffected by either fix and stays fresh every call):

| | tokens/call | rate ($/MTok) | cost/call |
|---|---|---|---|
| cached input (instructions, from call 2 on) | 1,624 | 0.01 | $0.0000162 |
| fresh input (volatile: gloss + candidates) | 2,236 | 0.10 | $0.0002236 |
| output (target: ≤150 tok/call, `"none"` reasoning) | ≤150 | 0.625 | ≤$0.0000938 |
| **Expected total/call** | | | **≤$0.00034**, vs. measured **$0.00066** |

That is roughly a 2× reduction, almost all of it from the output-token line: at the
measured ~655 output tokens/call, output alone cost $0.0004094/call — more than the
entire expected total after the fix. The cache fix moves the smaller of the two
numbers (input was never more than $0.00025/call at these volumes); the reasoning-off
fix moves the larger one.

**Live re-measurement (2026-09-02), scratch store, 5 source entries, 5 calls:**

| Metric | Before (measured 2026-09-01) | After (live, this fix) |
|---|---|---|
| `cache_hit_rate` | 0.0015 | **0.6842** (1/5 calls paid the fresh-instructions price; the other 4 hit the now-warm 1,792-token cached prefix) |
| output tokens/call | ≈655 | **36.4** avg (30-46 per call — reasoning tokens gone, only the answer remains) |
| cost/call | $0.00066 | **$0.0001032** avg — **≈6.4×** cheaper; the 4 calls past the first (steady-state cache) averaged **$0.000074/call**, ≈**9×** cheaper |

The output-token collapse (655 → ~36-46) is larger than the "≤150" target above:
`"none"` reasoning removed essentially all of the hidden reasoning tokens the old
`"low"` setting was paying for, not just the fraction the arithmetic assumed. Sample is
small (5 calls, one small batch each) and not a substitute for a full production
re-run, but it confirms both mechanisms fire as designed: the cache lights up past the
first call, and reasoning tokens — not the JSON contract — were the output-cost driver
all along.

### `spans` (fallback) — nano, low, 40 examples/call

**Context.** Example text + headword + inflected forms, for the examples the
deterministic `find_span` could not place — 10% of 3,000 examples (3/entry × 1,000
entries) = 300 examples.

**Static/volatile.** Instructions (~600 tok) cached; the batch of examples volatile.

**Model/effort.** `gpt-5.4-nano`, `low` — locating a substring, not writing prose.

**Batching.** 40 examples/call → ⌈300/40⌉ = 8 calls.

**Arithmetic** (40 × (20 tok example + ~5 tok headword/forms) = 1,000 tok fresh
input/call; output 40×6 = 240 tok/call):

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (1,600 + 7×1,000) | 8,600 | 0.10 | $0.00086 |
| cached input (7×600) | 4,200 | 0.01 | $0.00004 |
| output (8×240) | 1,920 | 0.625 | $0.00120 |
| **Total / 1,000 entries** | | | **≈ $0.0021** |

### `renditions` (gloss, examples, explanation) — luna, low, 1 call/(sense, field)

**Context.** The canonical content of that one field plus its existing renditions
(nothing, on a first enrichment pass). Nothing from any other field or sense — a gloss
rewrite does not need to see the examples.

**Static/volatile.** Instructions (~600 tok) cached; the canonical text volatile.

**Model/effort.** `gpt-5.6-luna`, `low` — writing prose, so the more capable model, but
`low` effort because the source text is already supplied; the model is rewriting, not
composing from nothing.

**Batching.** 1 call per (sense, field), covering all missing `(level, register)`
targets for that field in one shot (this is FR-3.4/FR-15.3, not four independent calls).

**Arithmetic — gloss**, 4 missing reading levels at `plain` (3 senses × 1,000 entries =
3,000 calls; ~30 tok canonical fresh input; output 4×30 = 120 tok):

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (630 + 2,999×30) | 90,600 | 0.10 | $0.00906 |
| cached input (2,999×600) | 1,799,400 | 0.01 | $0.01799 |
| output (3,000×120) | 360,000 | 0.60 | $0.21600 |
| **Total / 1,000 entries** | | | **≈ $0.243** |

**Arithmetic — examples**, same shape, canonical example ~20 tok, output 4×20 = 80 tok:

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (620 + 2,999×20) | 60,600 | 0.10 | $0.00606 |
| cached input (2,999×600) | 1,799,400 | 0.01 | $0.01799 |
| output (3,000×80) | 240,000 | 0.60 | $0.14400 |
| **Total / 1,000 entries** | | | **≈ $0.168** |

`lexical_explanation` is structurally identical but entry-level (1 call/entry, not ×3
senses), so it is roughly a third of the gloss line above; it is omitted from the
headline table in § 3 because the brief's comparison is gloss vs. gloss+examples vs.
+encyclopedia.

### `renditions` (encyclopedia) — luna, low, 1 call per level-set

**Context.** The 300–400-word canonical entry (~500 tok per the brief's assumption) —
the *whole* source text, because every target rendition is a full rewrite of it, not an
edit.

**Static/volatile.** Instructions (~600 tok) cached; the canonical entry is volatile and
large — it is also the reason this stage cannot be made cheap by caching, since the
volatile part *is* the expensive part.

**Model/effort.** `gpt-5.6-luna`, `low`.

**Batching.** 1 call per level-set (all default targets for one entry in one call). The
notable line from `SCHEMA-V3.md` §5: *"output ≈ N × source length"* — the expensive
stage, by construction, because it writes N full rewrites, not N short judgments.
Default target set is reading levels only, at `plain` — 4 targets, not the 20 a full
level-by-register cross would need.

**Arithmetic** (1,000 calls, one per entry; ~500 tok canonical fresh input; output
4 × 500 = 2,000 tok/call):

| | tokens | rate | cost |
|---|---|---|---|
| fresh input (1,100 + 999×500) | 500,600 | 0.10 | $0.05006 |
| cached input (999×600) | 599,400 | 0.01 | $0.00599 |
| output (1,000×2,000) | 2,000,000 | 0.60 | $1.20000 |
| **Total / 1,000 entries** | | | **≈ $1.256** |

**Why this is the one that would blow up under a register cross.** The default 4
targets (levels × `plain`) already cost $1.26/1,000 entries, almost entirely in output.
A full level-by-register sweep — 5 levels × 5 registers, minus the 1 canonical already
held — is 24 targets, six times the default's 4. Output cost scales with target count
(each is a full ~500-token rewrite), so a full cross would run to roughly **6× $1.26 ≈
$7.5/1,000 entries** for encyclopedia alone — more than the entire rest of a `generate`
run (§ 3). That asymmetry, not a stylistic preference, is why `encyclopedia_rendition_targets`
defaults to reading levels only and registers are off by default for this one field
(`DESIGN.md` § 5.4, FR-15.4).

### `senses` (existing stage, confusables added)

Unchanged model/effort/batching from the pre-v3 design (`DESIGN.md` § 4.3:
`gpt-5.6-luna`, medium effort — the quality-critical stage). v3 adds up to 3
`confusables` per sense (`{term, how_they_differ}`), ~60 extra output tokens/sense per
`SCHEMA-V3.md` §5, which is folded into the § 3 "full generate" line below rather than
priced separately.

## 3. Worked totals

| Sweep | What it covers | Cost / 1,000 (entries or existing entries) |
|---|---|---|
| **Full `generate`** | `overview` (incl. `kind`, per-sense `domain`) + `senses` (incl. relations, confusables, examples) + `etymology` + `encyclopedia` (canonical) + `lexical_explanation` — canonical content only, no rendition expansion. `tag_domain` is **not** part of this total: a freshly generated entry's domain comes from `DraftSense.domain`, an enum-constrained field on the `senses` call itself (`contracts.py`, D-17), so `generate` never runs the `tag_domain` stage at all. | **≈ $0.86** |
| **`retrofit`** (`classify_kind` + `tag_domain` + `spans`) | Bringing an existing store to v3 parity — the three passes `workflows/retrofit.py::RetrofitPass.ALL` actually runs, per § 2 above: $0.0005 + $0.050 + $0.0021 | **≈ $0.053** |
| **`resolve`** | A separate workflow/command (`workflows/resolve.py`), not part of `retrofit` — resolving relation targets to sense ids, per § 2 above | **≈ $0.058** |
| **`renditions`, gloss only** | 4 reading levels × `plain`, gloss field | **≈ $0.24** |
| **`renditions`, gloss + examples** | Same 4 targets, both fields | **≈ $0.41** |
| **`renditions`, gloss + examples + encyclopedia** | Adds the 4-target encyclopedia sweep | **≈ $1.67** |

`retrofit` and `resolve` are priced separately here because they are separate
workflows in the code: `RetrofitPass.ALL = (CLASSIFY_KIND, TAG_DOMAIN, SPANS)`
(`retrofit.py`) does not include `resolve`, and `resolve_store` (`resolve.py`) has no
`--only`-style pass selector shared with `retrofit` — a store owner who wants both runs
both sweeps.

The full-`generate` line is built the same way as the per-stage lines above:
`overview` ≈ $0.074, `senses` ≈ $0.311, `encyclopedia` canonical generation ≈ $0.311,
`etymology` ≈ $0.101, `lexical_explanation` ≈ $0.059 (each following the same fresh/
cached/output split as § 2, using the additional output-size assumptions in § 1).

The point of the last three rows: **encyclopedia renditions dominate.** They are ~75%
of the "everything" row's cost even at the conservative default (levels only, no
registers), and would dominate far more heavily — and roughly quadruple the whole row's
cost — under a full register cross. That is the concrete reason `encyclopedia_rendition_targets`
is a separate, narrower default from `default_reading_levels` × `default_registers`
(`SCHEMA-V3.md` §5; `DESIGN.md` § 5.4; FR-15.4).

## 4. What to measure before scaling

Every number above rests on assumed token sizes and an assumed warm cache. Before
committing a budget to a large `generate`, `retrofit`, `resolve`, or `renditions`
sweep, measure:

- **Deterministic ratios.** The actual `classify_kind` and `spans` deterministic-vs-
  fallback split, logged per `SCHEMA-V3.md` §5's requirement to measure and log them.
  This model assumed 85%/90%; a real corpus's punctuation, capitalization, and
  irregular-inflection profile could differ enough to change which stage dominates
  retrofit cost.
- **Cache hit rate**, from `usage.cache_read_tokens` on reported provider usage (the
  quantity `pricing.estimate_cost` treats as `cached_input_tokens`, per D-4: it is the
  source of truth here, not `RunUsage.cost`). Every fresh/cached split above assumes a
  warm, stage-consistent cache from the second call on; a run that rotates
  `openai_prompt_cache_key` values, runs too few calls per stage, or spaces calls past
  the cache's TTL will look substantially more expensive than these estimates.
- **Resolver confidence distribution**, from `RelationTarget.confidence` once `resolve`
  has run over a real store. A distribution skewed low would mean the "pick the right
  sense from a short list" framing in § 2's `resolve` arithmetic is optimistic, and that
  either more context per candidate or a higher-effort model is worth its added cost.
- **Actual encyclopedia canonical length and rewrite length ratio.** § 2's "output ≈
  N × source length" claim from `SCHEMA-V3.md` §5 is the single largest driver of total
  cost in this document; measuring real canonical lengths and real rewrite lengths
  against the assumed 500 tokens will move the § 3 encyclopedia row more than any other
  input.

## Measured on real v1.3 data (2026-09-02)

Offline run over entries migrated from `/nas4/data/workspace/curriculum/data/lexicon`
with the scripted test model (`tests/conftest.py`). **Call counts, ratios, and
percentages are real; token counts and dollar figures are not** — the scripted model
reports a fixed 1,200/400/300 tokens per call, so only the assumption checks below
carry over to the arithmetic above.

| Assumption in § 2 | Assumed | Measured | Sample |
|---|---|---|---|
| Deterministic span rate | 90% | **90.1%** (218/242) | 40 random seeds, 121 senses |
| Deterministic `kind` rate | 85% | **62.5%** on random seeds; **75%** on a relation-neighbourhood sample (329/440) | v1.3 is heavy in multi-word gap-fill entries, which are exactly the residue |
| Relation targets present in store | 50% | **63.8%** (517/810 distinct targets exist in v1.3) | 40 seeds |
| `resolve` calls per entry | ~1 | **0.86** (378 calls / 440 entries) | targets absent from the store are never sent |
| `resolve` idempotence | $0 second pass | **0 calls, $0** | same store, run twice |
| Migration failures | — | **0 / 440** | `from_v13`, sense order preserved |

Consequences for the estimates above:

- **`classify_kind` will cost more than modelled on the v1.3 store.** At 62–75%
  deterministic rather than 85%, the residue is 1.7–2.5× larger. It is still the
  cheapest pass by an order of magnitude (50 terms per nano call), so the absolute
  effect is cents per 1,000 entries, but the ratio is worth logging per run — the
  `classify_kind` pass already emits it as `metrics.deterministic_ratio`.
- **`resolve` will cost ~25% more than modelled** because more targets exist than
  the 50% assumed; conversely, more of the graph becomes a sense graph.
- The span-finder assumption holds; the LLM fallback sees roughly one example in ten.
