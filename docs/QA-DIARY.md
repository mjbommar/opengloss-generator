# QA diary — judging the core with a second model

Companion to `docs/CORE-DIARY.md`. That diary tracks what the pipeline can measure about
itself: coverage, spans, cycles, reading bands. Every one of those checks is
deterministic, and by its close-out the core store is essentially clean on all of them.
None of them can tell whether a definition is **true**, whether two senses are actually
**distinct**, or whether an example illustrates the sense it is filed under. This diary
tracks that second question, and it is answered by a model rather than by a function, so
it is answered on a *sample* and with the sample's cost written down.

Design decision: **D-48**. Implementation: `src/opengloss_generator/workflows/qa.py`,
`contracts.DraftQAVerdict`, `prompts.QA_INSTRUCTIONS`, `opengloss qa`.

## Method

### The judge

`StageName.QA` runs on **`claude-opus-5`** at the `default` service tier — the only stage
in the pipeline on a different provider from the generator (`gpt-5.6-luna` /
`gpt-5.4-nano`), because a model marking its own homework agrees with itself. The call
goes through the ordinary `StageRunner`, so it is budget-guarded, rate-limited, priced
and logged like every other call, and its provenance record lands on the entry it judged.
Structured output is `NativeOutput(strict=True)`, verified live against the Anthropic
model on 2026-09-02 (D-48 § 5).

### What the judge is shown

The static half is `prompts.QA_INSTRUCTIONS` — byte-stable and ~2.4K tokens: the judge's
stance, the rubric field by field, the closed MQM flag list with a one-line meaning each,
and one worked example verdict. It never varies, which is what makes two sweeps'
numbers comparable.

The volatile half is one entry, rendered compactly by `prompts.build_qa_prompt` and
capped near 3.5K tokens:

| Shown | Cap |
|---|---|
| headword, structural kind | — |
| per sense: part of speech and index, canonical gloss, examples, relations as `type->term`, domain tag | first 8 non-retired senses |
| per sense: gloss renditions at grade_1/plain, college/plain, neutral/technical | 3 |
| per sense: the grade_1/plain example rendition | 1 |
| the grade_1/plain and college/plain encyclopedia openings | 2 |
| the canonical encyclopedia opening, as the source the two above are checked against | 1 |
| any single long text | 120 words, ellipsis visible |

Renditions are referred to by their position in the list, never by a stored id, so the
judge cannot return a verdict about something it was not shown.

### The rubric

`entry_score` is 0-100, anchored in the instructions rather than left to a curve: 90+
accurate throughout, 80-89 cosmetic problems, 60-79 a defect a reader would notice, below
60 something factually wrong or a sense missing or invented.

Per sense, six booleans — `gloss_accurate`, `distinct_from_other_senses`,
`examples_natural`, `examples_fit_sense`, `relations_valid`, `domain_fits` — plus the
offending relation targets and a suggested domain when those two fail. Per sampled
rendition, three — `faithful`, `level_appropriate`, `register_appropriate`. Booleans,
not prose, because a defect **rate** needs every judged sense to answer every dimension;
free text is capped at 120 characters and is null on a pass.

The judge is told to be strict about exactly two things (factual error, sense conflation),
lenient about one (a gloss naming its own headword is a house-style matter the pipeline
already repairs deterministically, not an accuracy defect), and to judge accuracy and
fitness for a K-12-to-college learner rather than conformance to print-lexicography style.

### What a verdict becomes on disk

A verdict is **data, never an edit** — the judge rewrites nothing.

* **Entry** `Assessment`: `qa_score` = `entry_score`, `judge_model`, `judged_at`, and its
  `flags` unioned into `qa_flags`.
* **Sense** `Assessment`: `qa_score` = the share of the six booleans that came back true
  (100 when all six did), and the closed flag for each failed dimension —
  `factual_error`, `missing_content` + `og.duplicate_gloss`, `awkward_style`,
  `off_topic`, `terminology_error`, per `docs/STANDARDS.md` § 9b.
* **Rendition** `Assessment`: flags only, and only on a failure — `factual_error`,
  `audience_inappropriate`, `register_mismatch`. A rendition the judge passed is not
  touched, so no readability grade or deterministic flag is disturbed.
* **Provenance**: the priced record for the call, plus one **zero-cost** record whose
  `note` carries every free-text issue the judge wrote, so what the flags cannot express
  still survives on disk.

Two flags are deliberately never written from a verdict: `og.readability_miss` and
`og.headword_absent`. Both are owned by deterministic machinery that spends money on what
they select, and a judge writing them would enqueue priced rewrites on the strength of an
opinion (D-48 § 3).

### Sampling

`stratified_sample(store, core_words, n, seed)` stratifies by the product of three axes,
all readable off an entry already on disk: **lexeme kind**, **sense count** bucketed 1 /
2-3 / 4+, and **frequency tercile** taken from the word's rank in `data/core/core_10k.tsv`.
Every non-empty stratum gets a slot before proportionality is applied, so the rare strata
a purely proportional split would round away are still covered. The draw is seeded per
stratum and the result is sorted, so the same `(list, n, seed)` always yields the same
ids — which is the only thing that makes one iteration's numbers comparable with the next.

### Running it

```bash
opengloss qa --from-list data/core/core_10k.tsv --sample 50 --seed 1 \
  --store data/core-store --budget 8 --report runs/qa-iteration-1.json
```

`--dry-run` prints the sample and the projected cost without calling anything. `--force`
re-judges entries that already carry a verdict; without it a re-run over a judged sample
costs $0, since `run_qa` is idempotent on `judge_model + judged_at`.

### Metrics

`--report` writes, and the run summary prints: entries judged / skipped / failed, mean
`entry_score` and its distribution across `<60`, `60-79`, `80-89`, `90+`, the defect rate
per sense dimension (share of judged senses answering it false), the rendition defect rate
per target (`gloss grade_1/plain`, `example grade_1/plain`, `encyclopedia college/plain`, …),
the entry-level flag histogram, the first 20 free-text issues, and the cost — total and
per entry.

### What a judged entry costs

Measured, not modelled. The first live call — `vow`, 3 senses, 14 sampled renditions —
cost **$0.12286**: 8,022 input tokens and 3,310 output. The input is roughly three equal
parts (the static rubric ~2.4K, the entry ~1.6K, and the contract's JSON schema ~1.5K,
which native structured output sends on every call); the output is one record per sense
plus one per sampled rendition. At that rate a 50-entry sample is ≈ **$6.1** and the whole
10K core would be ≈ **$1,200** — so this is a sampling instrument, not a sweep.

`cached_input_tokens` came back **0**: `router.settings_for`'s Anthropic branch sets no
`cache_control` breakpoint, so the ~3.9K tokens of rubric-plus-schema that are identical on
every call are re-billed at full rate, about 16% of the call. The instructions are already
byte-stable and well over the 1,024-token minimum, so the fix is one setting in `router.py`
rather than a prompt rewrite. Recorded as a residual, not fixed here.

## Iteration 1

### Free QC scan — 2026-09-02

Each iteration pairs a deterministic scan ($0) with a judged sample. The scan runs
first because anything it can see should never cost a judge call.

| # | Finding | Count | Assessment |
|---|---|---|---|
| 1 | Canonical examples in a stilted academic register ("Two researchers formed a duo…", "A miniature object appeared in the dataset.") | **5,401** canonical (+754 college, 140 grade_10 renditions — acceptable at that level) | v1.3 legacy; the paper's own QA flagged it. Real defect. |
| 2 | Same target listed as both `synonym` and `hypernym` of one sense | **9,873** senses | Mixed truth: `tahoe`→`lake` is instance-of, `teach`→`instruct` is synonym, `chief`→`title` is hypernym. Needs a per-pair decision; proper nouns are decidable by rule. |
| 3 | Two rendition targets with identical text / rendition identical to canonical | **592 / 87** | Degenerate renditions (`fatigue:verb:0` formal == informal). |
| 4 | Sense lists its own lexeme as a synonym | **185** | v1.3 origin. Meaningless edge. |
| 5 | Same target as both `synonym` and `antonym` | **63** | Contradictions (`refrigerator` antonym `fridge`), several copied by reciprocity from a wrong assertion. |
| 6 | Garbage examples (`'hypernyms(['`, `'?'`, one-word) | **21** | v1.3 artifacts. |
| 7 | Proper nouns with 4+ senses (`beloved` 5 — the novel and the adjective) | 188 | `beloved` is mis-kinded; the evidence rule promoted a common word with a famous proper-noun use. Worth a judge look. |
| 8 | Rendition text with low word overlap vs canonical (heuristic) | 9,854 (2.8%) | Not actionable alone — legitimate rephrasing; the judge's *faithful* verdict measures this properly. |
| 9 | "Feedback leak" regex hits | 67 | Inspected: false positives ("Rewrite the fragment so it contains…", "language model" as content). No real leaks. |

Rendition length medians (words): gloss grade_1 10 · grade_5 12 · grade_10 14 · college 16 · technical 16; encyclopedia grade_1 189 · grade_5 238 · grade_10 294 · college 320 — a clean monotone ladder.
| 10 | Proper nouns carrying adjective/verb/adverb senses | **260 of 1,043 (25%)** | Two populations: demonyms and proper adjectives (`brazilian`, `hindu`, `arctic`) where `proper_noun` is defensible, and common words with one famous capitalised sense (`beloved`, `excel`, `julian`) where the evidence rule (D-26) over-promoted. Root cause is structural: `kind` is per lexeme but "proper" is a per-sense property. Candidate schema change for v4: `kind` per POS entry, or a `Sense.is_proper` flag. Judge sample to quantify the split. |

**Actions from the scan (iteration 2):** a `content_hygiene` pass with six steps —
self-synonyms and synonym/antonym contradictions demoted to `see_also` (free); the
synonym+hypernym pairs retyped by rule for proper nouns (`instance_of`) and by a nano
choice otherwise; garbage examples removed (text kept in a note) so `repair`
regenerates them; stilted canonical examples rewritten by luna with the headword kept;
degenerate identical renditions re-rendered. Finding #10 (lexeme-level `kind`) is a
schema question for v4, not a pass.

### Judge sample — 60 entries, stratified by kind × sense count × frequency tercile, seed 7

Run: 58 entries judged (2 failed — the verdict JSON truncated at the QA policy's
`max_tokens`; fixed by raising it for many-sense entries), 179 senses, 833 renditions
sampled. **$4.74, $0.082/entry**, cache hit 65% after enabling Anthropic instruction
caching in the router, 7 min at 8 workers.

**Scores:** mean **62.7**; buckets 90+: 0 · 80–89: 0 · 60–79: 41 · <60: 17. Lowest:
`ia` 18, `never` 38, `household` 46, `ian` 47, `mae` 48.

**Sense-level defect rates** (share of judged senses failing the dimension):

| Dimension | Rate | What the judge saw |
|---|---|---|
| relations_valid | **92.7%** | plurals and inflections as synonyms ("banners", "ads"), modifier phrases as hyponyms ("crisp benjamin"), enumerations as antonyms ("one-dollar bill"), meta-labels ("slang term", "modifier", "given name"), descriptive phrases ("indoor plant") |
| examples_fit_sense | 34.1% | examples filed under sense 2 that illustrate sense 1; "Monks take a vow to celibacy" |
| examples_natural | 29.6% | the stilted classroom register the free scan measured at 5,401 canonicals |
| distinct_from_other_senses | 25.7% | near-duplicate senses that exact-text dedup cannot see |
| domain_fits | 19.0% | e.g. `argue` → `law_government.courts_justice` |
| gloss_accurate | 12.9% | "restricts a general adjectival use to 'scholarly analysis'"; a `ba` entry whose encyclopedia treats the Egyptian *ba* with no such sense |

Edge-level, over the 1,734 relations on the judged entries: **44.9% named invalid**
(antonym 51%, hyponym 51%, synonym 40%, hypernym 35%). The graph is the weakest layer
of the resource, by a wide margin — the coverage and cycle work made it *consistent*,
not *true*.

**Rendition defect rates:** encyclopedia grade_1 **46.6%** not level-appropriate —
every one of them passes the Flesch-Kincaid band; FK measures sentence and syllable
length, not whether "Mesopotamia", "chastity", "obedience" are grade-1 words. Gloss
grade_1 10.6%, example grade_1 7.8%, gloss college 1.1%, gloss technical 1.7%,
encyclopedia college 0%. Encyclopedia factual defects: 2 of 58 ("years after the birth
of Anno Domini").

**Flags:** terminology_error 58 · awkward_style 29 · missing_content 23 ·
hallucination 16 · audience_inappropriate 15 · factual_error 7 · unsupported_addition 7.

**Findings ranked for iteration 2:**
1. Relation validity (edge-level 45%): free filters for inflections of the headword or
   of sibling targets, headword-modifier phrases, and an extended meta-label stoplist;
   then a nano validity/retype verdict per remaining relation. Fix in progress.
2. Grade-1/5 vocabulary: a familiar-word-list metric (Dale–Chall style) beside FK, a
   generation-time retry, and a rewrite pass. Fix in progress.
3. Junk headwords in the core list (`ia`, `ian`, `mae`): the external-attestation
   filter accepts abbreviations and name fragments present in word lists. Needs a
   `kind`/score-driven exclusion in `scripts/core_lexicon.py` and a review list.
Deferred to iteration 3 (need a model per sense pair): example–sense fit and sense
distinctness.

Quantified: the core holds 55 `abbreviation` entries (`dept`, `dod`, `ky`, `nc`, `hr`,
`dp`) and 545 headwords of ≤3 letters, most of them real words (`vow`, `jaw`, `nun`,
`ore`) with a tail of state codes and two-letter tokens (`ky`, `lm`, `ru`, `sc`). The
judge's lowest scores come from thin *content* on those entries rather than from the
tokens being invalid, so this is a review-list rule for `scripts/core_lexicon.py`
(exclude `kind=abbreviation` and ≤2-letter tokens from the core unless attested in the
Google 10K) rather than a hygiene pass.

**Fix in progress — relation validity (finding #1).** `workflows/relation_hygiene.py`
(**D-50**), four steps, `opengloss relation-hygiene`. Three are free and take the three
artifact classes the judge's own offending-target list is dominated by: a target that is
an inflected form of the headword or of a *sibling target of the same type* ("banners"
beside "banner", "ads" beside "advertisement"), a multi-word target that is just the
headword with a modifier on it ("crisp benjamin"), and a meta-label ("slang term",
"popular given name", "plural form"). Each is demoted to `see_also` with a note naming
the rule — nothing is deleted, and a phrase that is itself an entry in the store ("ice
axe" under *ice*) is kept, as are collocations, whose whole point is the shape the second
step otherwise rejects. What is left goes to one nano verdict per relation (chunked at 60
per call, ~10K calls ≈ **$0.8** for the whole core), which either passes it, demotes it,
or — for the "one-dollar bill as an antonym of benjamin" shape, a true claim under the
wrong type — retypes it. Idempotent on a D-47 marker whose digest is taken over the
relation set *after* the verdicts land, so a second sweep over an unchanged entry is free.
Run after `graph-hygiene`: that pass's reciprocity step can re-add a demoted synonym from
a far side that was never demoted with it (D-50, "Run order").

**Fix in progress — sense distinctness and example fit (iteration 3).**
`workflows/sense_hygiene.py` (**D-52**), two steps, `opengloss sense-hygiene`. Both are the
per-sense-pair judgements iteration 1 deferred, and both cost one nano call per multi-sense entry.
`distinctness` lists every live sense of an entry with its part of speech, gloss and first example
and asks which of them are the *same* meaning; the instructions set WordNet's bar — a sense is
distinct only when a learner would need a separate definition — and spend most of their length on
what is not a distinction (domain colouring, register, specific-versus-generic phrasing), because
the measured defect is exactly the *vow* pair that differs only by the word "religious". A group
keeps the **lowest index** (D-1: sense ids are positional and are never renumbered) and merges onto
it every example, relation and rendition it lacked before the others are marked `retired`; nothing
is deleted, and the entry's provenance carries `retired sense <sid>: duplicate of <survivor sid>`.
`example_fit` lists every live sense and every canonical example across all of them and asks which
sense each example illustrates, or `none` — the noun-use-filed-under-a-verb-sense shape included.
A misplaced example is moved with its level renditions and its span re-found, unless the sense it
belongs to already holds three canonical examples, in which case it is dropped into a note rather
than piled on; `none` removes it, text preserved in a note either way. A sense left without a
canonical example is reported (`senses_emptied`) for `retrofit --only repair` to regenerate, not
repaired here. Idempotent on a D-47 marker whose digest is over the set *after* the answers land
(D-50's variant), two attempts per entry, and a single-sense entry is never billed at all —
≈ 6K multi-sense core entries × 2 calls ≈ **$0.9** for a full sweep, $0 in steady state.

## Iteration 2 — 2026-09-02 — acting on the scan and the judge

**Content hygiene (D-49) applied:** $0.69, 64 min at 64 workers (luna steps are
latency-bound), 5,285 entries changed across the six steps — self-synonyms and
synonym/antonym contradictions demoted, proper-noun hypernyms retyped to `instance_of`
and the remaining synonym+hypernym pairs settled by nano, garbage examples removed,
stilted canonical examples rewritten, degenerate identical renditions re-rendered.
Then, in order: graph hygiene (now blocks re-adding a demoted pair from the far side —
the run-order hazard the relation agent found), relation hygiene (D-50: inflections,
headword-modifier phrases, meta-labels by rule; a nano validity/retype verdict on the
rest), repair for any senses emptied, vocabulary hygiene (D-51), and a paired re-judge
of the same 60 entries under the same seed so every number below is before/after on
identical items.

**Free rescan after content + relation hygiene** (before vocabulary and the re-judge):

| Finding | Before | After |
|---|---|---|
| synonym+hypernym pairs | 9,873 | **53** |
| self-synonyms | 185 | **0** |
| synonym+antonym contradictions | 63 | **0** |
| garbage examples | 21 | **0** |
| degenerate identical renditions / identical-to-canonical | 592 / 87 | 419 / 65 |
| stilted canonical examples | 5,401 | 5,494 |

**Three findings about the fixes themselves:**
1. The content-hygiene sweep reported `completed` at $0.69 but had stamped the
   `stilted_examples` step on only 573 entries (of ~3,500) and `degenerate_renditions`
   on 169: the luna steps stopped early with no warning in a WARNING-level log. The step
   is correct — on a scratch copy it rewrites "Two researchers formed a duo…" to "The
   comedy duo had everyone laughing by the end of the night." for $0.0001 — so the
   defect is in the sweep's stop path. Rerun in progress with INFO logging to catch it.
2. Relation hygiene's nano validity step hit its $3 cap after 8,304 entries: the
   reciprocity completion had doubled the relation count to 444K, so the step costs
   ~4× the $0.8 estimate. Remainder rerun in progress (idempotent).
3. Two side effects of demoting invalid relations: reciprocity fell to 79% (synonym) /
   85% (antonym) because only the near side is demoted — a far-side phase is being
   added (the pair was judged invalid, so both directions are) — and 143 hypernym
   cycles reappeared from `better_type` retypes; graph hygiene re-runs after the
   relation pass to break them ($0).

Far-side fix landed (D-50 amendment): every step that demotes a symmetric relation now
queues a second locked phase on the target entry that demotes the matching reciprocal
(same type, pointing back at the demoted sense). Pairs already left one-sided by the
first run are reconciled by a one-off scripted pass under the same rule (any symmetric
relation B→A where A holds a `demoted:` `see_also` toward that sense of B), then graph
hygiene re-breaks the cycles that `better_type` retypes introduced.

**Paused (billing check):** the staged paired re-judges (iterations 2b, 3) and the
fresh-seed sample (iteration 4) are stopped before their `claude-opus-5` steps pending
confirmation of an unexpected ~$50 Anthropic charge. The pipeline's own Anthropic spend
to date is the iteration-1 sample ($4.74) plus two live checks (~$0.25); the likely
source is the Claude Code session and its subagents billing to the `ANTHROPIC_API_KEY`
exported in `~/.bashrc`. The OpenAI hygiene passes already running continue.

**Finding about the fixes, #4 — provider rate ceiling.** Running three luna passes in
parallel at 64 workers each (content rerun, vocabulary hygiene, relation hygiene's luna
step) overran OpenAI's flex ceiling for `gpt-5.6-luna`: 5,387 `429 too many requests`
against 1,291 completed calls over an hour; the vocabulary pass finished with most
entries skipped and the content rerun never converged. Root cause: the rate limiter is
per *process*, so N processes each believe they own the full allowance. Rule adopted:
one luna pass at a time, ≤48 workers; nano passes may run alongside. (The SDK's own
retries mask this at INFO level — the earlier "stopped early with no warning" in
finding #1 was the same 429 storm seen from the other side.) Both passes are idempotent
and are being rerun sequentially.

Reconciliation result: 50,609 entries were the target of a demoted pair; **27,529
far-side reciprocals demoted** across 6,611 entries at $0; graph hygiene afterwards found
nothing to break. Since the first sweep, the relation layer has therefore lost roughly
70K edges to `see_also` — the judge's 45% edge-level invalid rate made concrete. The
remaining passes (content luna steps, vocabulary, relation remainder, the three judge
samples, sense hygiene) run serially from here at ≤48 workers.

**Finding about the fixes, #5 — the flex downgrade never fired.** A direct probe showed
`gpt-5.6-luna` on `service_tier=flex` returning 429 on a single trivial request (body:
`rate_limit_exceeded`, "We're currently processing too many requests") while the same
model on `default` answered in 0.8 s and nano on flex in 0.6 s. Our router's automatic
flex→auto downgrade only matched the documented `resource_unavailable` body, so every
luna pass sat retrying an unavailable tier — the real cause behind findings #1 and #4,
which were symptoms. Fixed: the downgrade now matches both shapes, and the threshold is
three consecutive rejections. Rerunning the serial chain on the patched code; luna work
will run at `auto` (standard price, ~2×) whenever flex is starved, which is the correct
trade — the alternative was zero throughput.
Confirmed on the relaunch: `flex_tier_downgraded` after exactly three rejections, then
~600 entry writes/min on `auto`; the content luna steps that had spun for over an hour
completed in three minutes.

**Iteration-2 passes, final numbers (serial, on `auto` where flex was starved):**

| Pass | Result | Cost |
|---|---|---|
| content hygiene, luna steps | stilted canonical examples: 2,321 rewritten with the headword kept (1,596 calls); degenerate renditions: 470 re-rendered, 27 rejected by the acceptance rules | $0.40 |
| vocabulary hygiene | 3,931 grade_1/grade_5 renditions rewritten; **3,722 now within the Dale–Chall band, 2,805 still over** after the bounded attempts (cache hit 87%) | $1.27 |
| relation hygiene, nano validity (remainder) | 12,321 relations demoted as invalid, **2,700 retyped** to the type nano judged correct, 12 inflection demotions with 6 far-side | $2.02 (stopped on its cap; the first run took the rest) |
| graph hygiene | 16 cycle edges and 67 mutual pairs demoted, 653 reciprocals added | $0 |

The judge stages of this chain failed on a configuration mistake of mine (a nested
env override replaced the whole QA policy instead of merging into it); fixed in
config, and the paired re-judge and the fresh-seed sample are queued behind sense
hygiene (iteration 3).
Second reconciliation after the validity remainder: **7,458 more far-side reciprocals
demoted** (34,987 in total). The in-pass far-side phase did not fire on the real store
(reported 0) although its unit test passes — under investigation as finding #6.

**Spot check of the nano retypes (6,212):** the dominant classes are plausible
corrections — `hyponym→synonym` 1,407 and `synonym→hyponym` 1,105 (the model sorting
near-synonyms from subtypes), `hyponym→meronym` 443, `synonym→instance_of` 233. One
class is wrong on inspection: **`hyponym→hypernym` onto multi-word targets** —
`corp` → hypernym `public corporation`, `bull` → hypernym `speculative investor`,
`dallas` → hypernym `Scottish surname`. Those targets are narrower or are descriptive
labels, so the original `hyponym` (or nothing) was right; nano inverted the direction
in 158 of 475 such retypes. A retype is a strong claim and this step should have been
asked for `valid | invalid` only, with retyping reserved for a second, per-pair
question — recorded for the pass's next revision. The proper-noun cases (`dallas`
→ `Scottish surname`, `masculine name`) are meta-labels the free stoplist missed
(`surname`, `name` as the head of a descriptive phrase); the stoplist gains a
"head-noun is a meta-label" rule.

**Finding about the fixes, #6 — the in-pass far-side phase was cancelled by the budget
stop.** Found and fixed. The second phase was driven through the same pool wrapper as the
first and handed the same stop event; `run_pool`'s workers return before pulling their
first item once that event is set, and a budget stop sets it. Both real `validity` runs
stopped on their cap, so both ran the phase over zero items and reported
`far_side_demoted=0` while banking 12,321 near-side demotions — the 4,468 open reciprocals
the read-only scan found, and the 7,458 the reconciliation script then had to clean up by
hand. The unit tests passed throughout because none of them stops a step; the same run's
free `inflections` step, which finished with the event still clear, reported its 6
far-side demotions correctly, which is what ruled out the mechanism itself. The far-side
phase now takes no stop event at all: it costs nothing, it repairs writes already
committed, and its work list is bounded by the demotions the run actually made, so there
is nothing a stop there can usefully save. The step's `stopped_reason` still reports the
stop. Two regression tests reproduce the real shape (budget stop and caller stop part way
through, reciprocals both resolved to the demoted sense and unresolved); see D-50's second
amendment. The next `relation_hygiene` sweep should need no reconciliation pass behind it
— and if the store is otherwise clean, should report `far_side_demoted` roughly equal to
its own count of demoted symmetric relations toward resolved entries.

## Iteration 3 — 2026-09-02 — sense distinctness and example fit

`sense_hygiene` (D-52), nano, one call per multi-sense entry per step, $2.99 in 10 min:

| Step | Result |
|---|---|
| distinctness | 4,233 duplicate groups merged; **4,940 senses retired** as tombstones (38,955 → 34,015 live senses, −12.7%) with their examples, relations and renditions folded onto the surviving lowest-index sense; 83 groups refused by the guards (cross-POS, bad ref) |
| example_fit | on the first 4,250 entries before the cap: 712 canonical examples moved to the sense they actually illustrate, 848 removed as fitting no sense (text kept in notes), 272 senses left empty → `repair` regenerated 528 examples for 221 entries ($0.02) |

The retirement rate is the striking number: the judge's 25.7% "not distinct" was, if
anything, conservative — one live sense in eight was a restatement of another. Because
retirement is a tombstone (D-1), every sense id and every rendition on the survivor is
unchanged; downstream consumers see fewer, better-separated senses at the same
addresses. The `example_fit` remainder (~1,750 entries) is queued after the judge
samples so the two do not contend for locks.

### Paired re-judge — same 60 entries, same seed, after iterations 2–3

$5.57, 60/60 judged (the `max_tokens` fix removed the 2 truncations). **Mean score
62.7 → 64.4**; entries under 60: **17 → 6**; none yet reach 80.

| Sense dimension (share failing) | Before | After |
|---|---|---|
| distinct_from_other_senses | 25.7% | **11.8%** |
| relations_valid | 92.7% | 91.2% |
| examples_fit_sense | 34.1% | 35.3% |
| examples_natural | 29.6% | **41.8%** (worse) |
| gloss_accurate | 12.9% | 14.7% |
| domain_fits | 19.0% | 18.2% |

| Rendition (share defective) | Before | After |
|---|---|---|
| encyclopedia grade_1 level-appropriate | 46.6% | 46.7% |
| gloss grade_1 | 10.6% | 13.5% |
| gloss college / technical | 1.1% / 1.7% | 0% / 4.7% |
| hallucination flags | 16 | 8 |

**This is the honest result of the program, and it changes the plan.** The hygiene
passes moved exactly the dimensions a deterministic scan could define — duplicate
senses (−14 points), hallucination flags (halved) — and nothing else. Three specific
reasons, each visible in the judge's notes:

1. *Relation validity did not move* because the judge's bar is semantic, not
   morphological: after inflections, phrases and meta-labels were removed, the
   remaining targets are real words that are still wrong for the sense ("an adjective
   is given noun hypernyms"; "biology concept, science topic" survived as targets). The
   nano validity verdict agreed with the original generator far more often than the
   opus judge does. Relation quality needs the stronger model, or regeneration of the
   relation set per sense from the gloss rather than repair of v1.3's list.
2. *Examples got less natural* because the stilted-example rewrite replaced one
   template ("Researchers…") with another the regex cannot see (definition-like
   sentences, near-duplicates across senses: "several examples are near-duplicates or
   definition-like rather than natural sentences"). Rewriting by pattern moves the
   pattern.
3. *Grade-1 encyclopedias did not move* because vocabulary hygiene rewrote glosses and
   examples first and stopped on its cap before most encyclopedias ("grade_1 text uses
   petition, entreaty, mercy, formal, reasoning"); 2,805 renditions were still over
   band when it stopped.

Also new in the after-sample: a *missing sense* class ("the common sense 'to ask
strangers for money' is absent" for `beg`) — coverage of meanings, which no pass
addresses and which is a generation-time property.

Measured against every canonical example (77,623): a broad academic-register regex
built from the judge's language matches 2,641, but inspection shows most are fine
("Students categorize posts with a hashtag.") — a wider net is not a better one.
The one class that is defective by construction is the **sentence fragment** (498:
lowercase start / no terminal punctuation — "the mile-long bridge opened to traffic").
Decision for iteration 4: regenerate only that class deterministically-detected; do not
build another register regex. Example naturalness beyond that is a judge-only property
and belongs to a future per-sense regeneration, not a repair pass.

## Iteration 4 — 2026-09-02 — fresh sample, and what four iterations established

**Fresh-seed judge sample** (seed 11, 60 entries of which 17 were already judged
under seed 7 and were skipped by idempotence; 43 judged, $3.88):

| | Iteration 1 (seed 7, before) | Iteration 4 (seed 11, unseen entries) |
|---|---|---|
| mean score | 62.7 | **66.4** |
| entries under 60 | 17 / 58 | 7 / 43 |
| distinct_from_other_senses failing | 25.7% | **7.7%** |
| relations_valid failing | 92.7% | **83.0%** |
| gloss_accurate failing | 12.9% | 10.1% |
| examples_fit_sense failing | 34.1% | 31.8% |
| examples_natural failing | 29.6% | 33.3% |
| domain_fits failing | 19.0% | 31.8% (see below) |
| encyclopedia grade_1 not level-appropriate | 46.6% | **39.5%** |
| hallucination flags | 16 | 9 |

On entries the hygiene passes had never been tuned against, every dimension a pass
targeted moved in the right direction, and the two the passes could not reach
(example naturalness, example–sense fit) stayed flat. `domain_fits` rose — the
taxonomy-v2 retag put proper-noun countries under `law_government`/`people_society`
leaves while the judge wants "geography", a root the taxonomy does not have
(`nature.landforms` is the nearest). That is a taxonomy gap, recorded for v3 of the
taxonomy, not a tagging error.

**Standing defects the judge keeps naming, none of which a repair pass addresses:**
1. *Relations remain the weakest layer* (83% of senses still carry at least one
   invalid target). The v1.3 relation lists were generated per sense by a weaker model
   with no sense inventory to point at; repair can subtract but cannot add the right
   targets. The fix is regeneration of each sense's relations against the resolved
   sense inventory, by a stronger model — a generation task, ≈ $0.01/sense on luna.
2. *Missing senses* ("no brazilwood sense", "'to ask strangers for money' is absent",
   "the headword does not name a person; that meaning belongs to 'Argentine'"): a
   coverage property of generation, invisible to every deterministic check.
3. *Encyclopedia–sense mismatch*: the encyclopedia treats a referent the senses do not
   list. Same root cause as 2.
4. *Example naturalness* beyond the fragment class: judge-only.

**What four iterations established about method:** a deterministic scan finds and a
repair pass fixes *form* (duplicates, artifacts, fragments, vocabulary bands, cycles,
reciprocity) reliably and for cents; a second-model judge finds *content* defects
(truth, sense coverage, fitness) that repair passes cannot fix and pattern rewrites
make worse. The two must be paired, and the judge's findings should drive
*regeneration* decisions, not more repair passes. The cost split was ≈ $22 on Claude
for four judge samples against ≈ $16 on OpenAI for every repair pass in this program.

**Iteration-3 remainder (run after the samples):** `example_fit` over the remaining
multi-sense entries — 1,041 more canonical examples moved to the sense they illustrate,
1,244 removed as fitting no listed sense, 394 senses emptied and regenerated by `repair`
(684 examples, $0.02); $1.44. Totals for the step: **1,753 moved, 2,092 removed** —
about 5% of all canonical examples were filed under the wrong sense or under none,
which is the deterministic corroboration of the judge's 34%-of-senses figure (a sense
with one misfiled example fails the dimension). The judge's "examples_fit_sense" rate
on the fresh sample (31.8%) predates this remainder for most of its entries.

Iteration-3's decision acted on: `content_hygiene` gained a seventh step,
`fragment_examples`, regenerating the 498 measured fragments in place (D-49 addendum).
**Vocabulary-hygiene remainder:** 300 more renditions rewritten (249 now in band),
$0.11; **2,172 renditions remain over their Dale–Chall band after the two bounded
attempts** — the residual the pass cannot reach with rewriting alone. On inspection
these are grade-1 encyclopedias whose *subject* is hard (a country's colonial history,
a physics quantity): the fix is not more rewriting but a shorter grade-1 encyclopedia
that says less, which is a generation-time decision (a grade-1 target length of ~120
words rather than a rewrite of 190).

## Close-out — 2026-09-02

**Four iterations, each a free scan paired with a 60-entry `claude-opus-5` sample.**

| Measure (10,000 core entries) | Before iteration 1 | After iteration 4 |
|---|---|---|
| live senses | 38,955 | **34,015** (4,940 duplicates retired as tombstones) |
| relations | 378,800 (0 resolved) → 444K after reciprocity | 434,931; **218,864 / 223,314 in-core resolved (98%)**; ~70K judged-invalid edges demoted to `see_also`, never deleted |
| synonym / antonym reciprocity | 24% / 13% | **98.1% / 99.4%** |
| hypernym cycles / self-loops | 2,556 / 40 | **0 / 0** |
| self-synonyms · synonym∧antonym · synonym∧hypernym | 185 · 63 · 9,873 | 0 · 34 · 401 |
| stilted canonical examples (regex) | 5,401 | 965 |
| garbage / fragment examples | 21 / 498 | 0 / 739 rewritten as complete sentences, 11 rejected ($0.06) |
| examples misfiled under the wrong sense | — | 1,753 moved, 2,092 removed, 1,212 regenerated |
| degenerate identical renditions | 592 | 23 |
| grade-1/5 renditions over the Dale–Chall band | ~34% of grade-1 encyclopedias | 3,971 rewritten; 2,172 still over (subject-bound) |
| readability-flagged renditions | 778 | 648 |
| judge mean score, paired 60 | 62.7 | 64.4 (unseen 43: 66.4) |
| judge: senses not distinct | 25.7% | 11.8% (unseen: 7.7%) |
| judge: relations invalid | 92.7% | 91.2% (unseen: 83.0%) |
| judge: hallucination flags | 16 | 8 (unseen: 9) |

**Spend for the QA program:** ≈ $19 on `claude-opus-5` (four samples, 221 judged
verdicts at ~$0.09 each with instruction caching) and ≈ $16 on OpenAI for every
repair pass — ≈ $35 on top of the ≈ $48 that built the core.

**Six findings about the tooling, all fixed (D-47–D-52 amendments):** hygiene passes
that stop on a budget must still run their far-side repair phase; a rendition-checking
pass must run after every rendition-writing pass and key its marker on the offending
set; the flex→auto downgrade must match the 429 body OpenAI actually sends; one luna
pass at a time; a nested env override replaces a whole policy; the judge's `notes` cap
must clip, not retry.

**What repair could not fix, and what should happen next** (each is a generation
task, not another pass): relation sets regenerated per sense against the resolved
inventory by a stronger model (the single largest quality lever — 83% of senses still
carry an invalid target); missing senses and encyclopedia–sense mismatches (sense
coverage is decided at generation); example naturalness beyond fragments; a `geography`
root in the taxonomy; grade-1 encyclopedias generated shorter rather than rewritten;
`kind` per POS entry rather than per lexeme (`beloved`, `excel`). The judge sample
should be re-run after any of those, on seed 7, so the numbers stay paired.

Final audit after the fragment pass: unchanged from the closing audit above (cycles 0, reciprocity 98.1% / 99.4%, in-core resolution 98.0%). All chains idle; nothing running against the store.

**Post-close coverage check (counted from the store, 2026-09-02):** definitions 100%
(8 renditions on all 34,015 live senses); encyclopedia 100% (canonical + 4 levels on
all 10,000); etymology, lexical explanation, kind, domain 100%; example renditions
96.6% → 99.84% after one `enrich --fields examples` pass ($0.23, 4,460 renditions) —
the senses whose canonicals `sense_hygiene` replaced had never had their levels rebuilt.
The residual 54 senses had *no* canonical example and `repair` skipped them: its
idempotence marker was a per-entry boolean, so an entry repaired once was never
revisited when a different sense was later emptied. Fixed (marker keyed on the set of
example-less sense ids, D-47 style) and rerun.

## Iteration 5 — tier-2 mid-chain QA/QC (2026-09-03 04:25–04:40)

Run while the tier-2 chain was on vocabulary-hygiene (stage 16 of 21), so encyclopedia
levels, readability/vocabulary rewrites, and the two hygiene remainders were still
pending. Reads only; the chain kept running.

### Defect found: one entry failed schema validation

`crave.json` (written 04:14:27 by example-hygiene) held two `neutral/plain` example
renditions with identical text, which `Renditions._keys_are_unique` rejects on read. Any
whole-store pass (audit, qa, vocabulary-hygiene) aborted on it with `StoreError`. A scan
of all 41,886 files found exactly one such entry.

- **Cause:** `example_hygiene._apply_example_rewrite` adopted a model rewrite whose text
  equalled a sibling rendition at the same level/register; nothing checked for the
  collision before mutating the rendition in place.
- **Fix:** `_ExampleOffender` now carries its `sense`; `_collides()` refuses a rewrite
  that would duplicate a sibling's uniqueness key (old text kept, offender stays
  flagged for the next sweep). Regression test
  `test_a_rewrite_that_duplicates_a_sibling_example_is_refused`. Full suite green
  (786 tests), ruff/ty clean. `crave.json` repaired by dropping the duplicate.

### Coverage audit (31,886 tier-2 entries, 76,855 live senses)

| field | renditions | coverage |
|---|---|---|
| gloss | 9 per sense (4 levels + neutral + 4 registers) | 99.95–100% each |
| examples | 4 level renditions + ≥2 canonical | 101.6% each level (some senses have 2), 237% canonical |
| encyclopedia | neutral only | 100% (grade_5 + college pending in chain) |
| lexical_explanation | neutral | 100% |

Consistency: 0 duplicate canonical glosses, 3 entries with zero examples, 3 senses with
zero relations, headword-initial gloss renditions 395 / 492,176 (0.08%), readability-miss
flags 19,591 (vocabulary/readability hygiene pending). Graph: 0 hypernym cycles, 0
self-loops; synonym reciprocity 98.4%, antonym 99.7%. Relations: 944,984 total,
524,763 resolved (99.0% of in-core targets, 97.7% of in-store targets; 411,380 targets
name lexemes not in the store).

### Free QC scan (whole store, 41,885 entries, 110,869 live senses, 997,764 gloss renditions)

| check | count | note |
|---|---|---|
| rendition_low_overlap_with_canonical | 28,825 | expected: register rewrites paraphrase |
| stilted_register_in_example | 2,475 | "researchers/the study" remnants after content-hygiene |
| synonym_also_hypernym | 1,489 | relation-hygiene validity remainder pending |
| proper_noun_with_4plus_senses | 892 | tier 2 is name-heavy; sense-hygiene remainder pending |
| stilted_register_in_gloss | 615 | |
| synonym_also_antonym | 207 | |
| feedback_leak_in_text | 186 | inflated: regex `as an ai` matches "as an airhead"; true leaks are a handful |
| identical_text_across_rendition_targets | 45 | |
| identical_example_across_levels | 36 | |
| function_word_with_4plus_senses | 28 | |
| very_short_example / very_short_gloss | 17 / 7 | |
| rendition_identical_to_canonical | 8 | |
| self_synonym | 2 | |

### Reading-level spot check (300 tier-2 entries, 887 grade-1 glosses)

Median FK 2.6 / 5.9 / 10.8 / 14.6 across grade_1 / grade_5 / grade_10 / college;
headword-initial 0.0–0.3%.

### Opus judge, 40 tier-2 entries (seed 7, 98 senses, $3.16 = $0.079/entry)

Mean **66.0** — the same as the core after four iterations (66.4; the core started at
62.7). Buckets: 7 below 60, 32 in 60–79, 1 in 80–89.

| sense-level defect | rate | pending pass that targets it |
|---|---|---|
| relations_valid | 88% | relation-hygiene validity remainder (queued in follow-up) |
| examples_fit_sense | 39% | per-sense examples (running) + sense-hygiene example_fit remainder |
| examples_natural | 36% | same; "corpus-style research prose" is the judge's recurring note |
| domain_fits | 29% | taxonomy retag (not scheduled; D-46 retag would cost ~$1 at nano) |
| gloss_accurate | 13% | |
| distinct_from_other_senses | 10% | sense-hygiene distinctness remainder |

Rendition defects: grade_1 example 10%, grade_1 gloss 6%, technical gloss 2%, college
gloss 0%. Flags: terminology_error 39, awkward_style 27, hallucination 11,
factual_error 7. Encyclopedia: 4 / 40 with a defect (one etymology date claim wrong).
Recurring judge notes: proper-noun senses given generic-term "synonyms" (a specific city
listed as synonym of *city*, *metropolis*); inflected forms and near-duplicates in
relation lists (`assessed, assesses, assessing`); research-register example filler.

**Read:** structurally tier 2 is at core parity (coverage, FK bands, graph), and the judge
score already equals the core's final score before the passes that raised the core are
applied to it. The relation-validity remainder is the highest-leverage pending item.

## Iteration 6 — tier-2 final re-judge (2026-09-03 16:45)

Same 40 entries and seed as iteration 5 (`--force`), after the full chain, three
follow-ups and the post-outage resume. 98 senses, $3.36.

| | iteration 5 (mid-chain) | iteration 6 (final) |
|---|---|---|
| mean score | 66.0 | **68.6** |
| entries below 60 | 7 | **5** |
| entries 80+ | 1 | 2 |
| relations_valid defect rate | 88% | 84% |
| examples_fit_sense | 39% | 36% |
| examples_natural | 36% | 42% |
| domain_fits | 29% | 29% |
| gloss_accurate | 13% | 14% |
| grade_1 example defects | 10% | 9% |
| grade_1 gloss defects | 6% | 8% |
| college gloss defects | 0% | 0% |
| encyclopedia college defects | — | 2.5% |

**Read.** +2.6 points on the same entries, above the core's final 66.4. The relation
criterion barely moved (88% → 84%) even though the validity pass demoted or retyped
~430K edges store-wide: the judge marks a sense defective if *any* listed relation is
wrong, and the per-sense lists are long (20–40 edges), so a single miss keeps the sense
flagged. Two follow-ups would move it: a stricter relation cap per sense (keep the
top-k the judge accepted), and the asymmetric-verdict reconcile pass noted in
CORE-DIARY. `examples_natural` rose slightly (36% → 42%): the per-sense examples stage
added 8 sentences per sense and the judge's "corpus-style" complaint applies to more of
them; the F8 filler detector plus a rewrite pass is the intended answer. Domain fit
(29%) is unchanged because no pass targets it; the versioned retag (D-46) is the fix.

## Iteration 7 — after the whole-store domain retag (2026-09-03 19:40)

Same 40 entries / 98 senses, seed 7, forced. $3.48.

| | iteration 6 (before) | iteration 7 (after retag) |
|---|---|---|
| mean score | 68.6 | 68.5 |
| entries 80+ / below 60 | 2 / 5 | **4 / 4** |
| domain_fits defect | 28.6% | **16.3%** |
| relations_valid | 83.7% | 85.7% (relations untouched; noise) |
| examples_natural | 41.8% | 34.7% |
| examples_fit_sense | 35.7% | 34.7% |
| gloss_accurate | 14.3% | 14.3% |

The retag did what its pilot predicted: domain defects down by 43% relative, other
criteria within noise. Next: reconcile (relations), then filler rewrite (examples).

## Iteration 8 — after relation-reconcile (2026-09-03 19:54)

Same 40 entries / 98 senses, seed 7, forced. $3.35.

| | it. 6 (tier-2 final) | it. 7 (retag) | it. 8 (reconcile) |
|---|---|---|---|
| mean score | 68.6 | 68.5 | **69.5** |
| entries 80+ | 2 | 4 | 4 |
| relations_valid defect | 83.7% | 85.7% | **67.3%** |
| domain_fits | 28.6% | 16.3% | 18.4% |
| examples_natural | 41.8% | 34.7% | 35.7% |
| examples_fit_sense | 35.7% | 34.7% | 32.7% |
| distinct_from_other_senses | 9.2% | 9.2% | 6.1% |

Relations was the criterion nothing had moved through ~$45 of validity judging; taking
the demoted edges out of the list the judge reads and capping per type dropped it 18
points. It is still the top defect: with 5–6 typed edges per sense, one wrong edge
still fails the sense. Next lever is a per-edge accept list from the judge's own
`invalid_relations` output feeding a targeted demotion — recorded as an open item.

## Iteration 9 — after the filler rewrite (2026-09-03 21:05)

Same 40 entries / 98 senses, seed 7, forced. $3.24.

| | it. 6 | it. 7 (retag) | it. 8 (reconcile) | it. 9 (filler) |
|---|---|---|---|---|
| mean score | 68.6 | 68.5 | 69.5 | **69.6** |
| relations_valid defect | 83.7% | 85.7% | 67.3% | 68.4% |
| domain_fits | 28.6% | 16.3% | 18.4% | 19.4% |
| examples_natural | 41.8% | 34.7% | 35.7% | 35.7% |
| examples_fit_sense | 35.7% | 34.7% | 32.7% | 34.7% |

The filler rewrite did not move `examples_natural` on this sample (35.7% → 35.7%):
it targets 15 corpus-wide 4-grams, and the judge's naturalness complaint is broader
("research-prose" register, not a specific phrase). The rewrite is still worth having
for the encoder (79,533 fewer near-identical sentence frames), but the naturalness
lever is a register-aware rewrite of the D-53 sentences, not a phrase filter. Net over
the three passes: **68.6 → 69.6**, relations 84% → 68%, domain 29% → 19%.

## Iteration 10 — free structural scan while the pair stages run (2026-09-03 21:30)

Read-only, 6,000 random entries / 15,718 live senses.

| check | count | rate | action |
|---|---|---|---|
| canonical gloss uses the headword (circular) | 2,040 | **13% of senses** | new `circular_gloss` content-hygiene step (agent, D-70) |
| stilted example ("the committee", "researchers", "the study") | 1,971 | 12.5% of senses | rerun `content-hygiene --only stilted_examples` after the pair stages (markers make it cheap) |
| duplicate (type, target) edge | 854 | 5.4% | `dedup` step queued after the pair stages |
| sense with zero relations | 377 | 2.4% (≈1,760 store-wide) | open: targeted relation regeneration |
| example with no span | 240 | — | rerun `retrofit --only spans` after the pair stages |
| contrast verdict `related_differently` / `unrelated` | 69 / 1 (pilot entries only) | — | new `verdicts` step in relation-reconcile (agent, D-68) |
| synonym also antonym | 21 | 0.1% | covered by reconcile/verdicts |
| qa answer meta-reference ("according to the sources") | 10 of the pilot's pairs | ≈ pilot's 7.9% | free post-check in `qa-pairs` before the whole-store run (agent, D-69) |
| "label leak" regex hits in examples/queries | 203 / 20 | — | false positives ("college crews", "reading level" used legitimately); dropped as a check |

The circular-gloss rate is the surprise: it is a defect the judge folds into
`gloss_accurate` (14%), and it directly weakens the gloss-as-positive retrieval pairs.

## Iteration 11 — free QC over the new pair data (2026-09-04 07:30, qa-pairs 40% done)

3,000 random entries: 20,992 QA pairs, 95,236 queries, 6,131 contrasts.

| check | count | rate |
|---|---|---|
| answer still carries a meta-reference ("according to the sources") | 4 | 0.02% (D-69 post-check working; pilot was 7.9%) |
| very short answer (< 6 words) | 549 | 2.6% — grounded factual answers ("They returned to the harbor."), acceptable |
| question without a question mark | 4 | 0.02% |
| duplicate query within a sense | 0 | — |
| one-word query | 0 | — |
| contrast verdicts | related_as_typed 70%, related_differently 30%, unrelated 0.3% | to be applied by the post-pair reconcile |

Read 12 pairs by hand (harbor, reluctant, mortgage): grounded, varied across the seven
types, the comparison answers ("harbor emphasizes the sheltered water; port the
facilities") are the best retrieval negatives in the set. One caveat: some factual
pairs quote the entry's own example ("What was Mia reluctant to do?" → "to go down the
slide") — grounded but not general knowledge; fine for retrieval pairs, weak for QA
evaluation. Worth a `question_type`-aware filter at export time rather than a change to
the generator.

## Iteration 12 — final re-judge for goal 2 (2026-09-04 12:10)

Same 40 entries / 98 senses, seed 7, forced. $3.40. After: retag, reconcile, filler,
pair stages, verdict-driven reconcile, circular-gloss rewrite, stilted rerun, spans.

| | it. 6 (start of goal 2) | it. 12 (end) |
|---|---|---|
| mean score | 68.6 | **70.2** |
| entries 80+ / below 60 | 2 / 5 | **4 / 5** |
| relations_valid defect | 83.7% | **60.2%** |
| domain_fits | 28.6% | **21.4%** |
| gloss_accurate | 14.3% | **12.2%** |
| examples_natural | 41.8% | 33.7% |
| examples_fit_sense | 35.7% | 37.8% |
| distinct_from_other_senses | 9.2% | 10.2% |

Over the six re-judges since the tier-2 chain ended: +1.6 mean, relations −23 points
(the contrast verdicts did the last 8), domain −7, gloss accuracy −2 (circular-gloss
rewrite), naturalness −8. The two that did not move — examples fitting the sense and
sense distinctness — are the next targets; both are per-sense judgement calls the
D-53 sense-check and sense-hygiene already make with nano, so the lever is a stronger
checker on those steps, not a new pass.
