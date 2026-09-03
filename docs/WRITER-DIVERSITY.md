# Writer diversity pilot (D-63)

**Question.** Every prose rendition in `data/core-store` is written by one model,
`gpt-5.6-luna`. Does rotating writers across model families buy linguistic diversity
for encoder training and evaluation at acceptable quality and cost, and how would it be
done cleanly? This is the pilot that answers that, on a frozen 300-entry sample, never
against `data/core-store`.

## Design

**Sample.** 300 headwords drawn `random.Random(11).sample(...)` from
`data/core/tier2_50k.tsv` rows 5,000-25,000 (`scripts/build_sample_writers.py`), copied
read-only from the main checkout's `data/core-store` into `data/sample-writers/`,
preserving the store's own blake2b shard layout. Each of the five arms below is its own
copy (`data/sample-writers-<arm>/`) so a writer's output can never clobber another's.
Every non-`luna` arm was reset to its common starting point before generation
(`scripts/reset_writer_arm.py`): canonical `(neutral, plain)` gloss and examples only,
nothing graded, no D-53 sentences, and the D-53 completion marker stripped so
`run_examples` is due again — otherwise a different writer against an already-complete
copy costs nothing and compares nothing.

**Frozen spec, only the writer varies.** Two tasks, both already-shipped, unmodified
workflows with only `ModelPolicy.model` changed (`scripts/run_writer_pilot.py`):

* **(a)** graded `EXAMPLES`-field renditions at `grade_1`/`grade_5`/`grade_10`/`college`
  (`enrich --fields examples --reading-levels ...`, `replace=True`).
* **(b)** the D-53 per-sense example-sentence workflow, 8 sentences per live sense.

**Writers.** `gpt-5.6-luna` (baseline — the store's existing content already covers
these senses under luna, so the luna arm is used **as-is, not regenerated**: it is
`data/sample-writers-luna/`, a straight copy, spent nothing beyond the QA judge and
analysis cost below), `qwen/qwen3.5-397b-a17b` (OpenRouter), `claude-haiku-4-5`
(Anthropic, direct), `gemini-3.7-flash` (Google, direct), `deepseek/deepseek-v4-pro`
(OpenRouter). Each non-luna writer's two tasks were run independently, each capped at
`--budget 0.75` (so $1.50 total per writer across both tasks, matching the pilot's
per-writer cap), `--concurrency 8`.

**Judge and analysis.** `opengloss qa --sample 40 --seed 42` against every arm that
produced content (luna, haiku, gemini, qwen — deepseek produced nothing to judge),
capped at `--budget 8` per arm (actual spend far under that, see below). Attribution,
lexical-diversity, anchoring and gate-breakdown numbers come from
`scripts/writer_diversity_report.py`, which attributes every rendition to the model
that actually wrote it via its own `provenance_id -> Provenance.model` link (not by
which arm's store it was read from — the canonical items in every non-luna arm are
still luna's, untouched by this pilot), run with `uv run --with scikit-learn` since
scikit-learn is one-off analysis tooling, not a package dependency. `opengloss qc
filler` (no model calls) ran against the stores directly.

## Per-writer results (ledger-sourced; every number below is measured over the calls
this pilot actually made, not extrapolated to a full store)

| writer | task (a) calls | task (a) cost | task (a) output tok/call (mean) | task (b) calls | task (b) cost | task (b) sentences accepted/generated | judge mean score (n judged) | judge cost |
|---|---|---|---|---|---|---|---|---|
| gpt-5.6-luna | — (existing) | $0 | — | — (existing) | $0 | — | **64.21** (33, 7 failed) | $2.776 |
| claude-haiku-4-5 | 199 | $0.741888 | 163.5 | 157 (+ sense_check) | $0.768795 | 1,315 / 1,960 (67.1%) | **62.83** (35, 5 failed) | $2.713 |
| gemini-3.7-flash | 176 | $0.761650 | 763.5 | 0 — **100% failure** | $0 | — | **64.62** (37, 3 failed) | $2.692 |
| qwen/qwen3.5-397b-a17b | 108 | $1.5325 | 3,835.6 (heavily blowup-skewed; typical is 200-350) | 23 | $0.2254 | see below | **64.53** (38, 2 failed) | $2.888 |
| deepseek/deepseek-v4-pro | 0 — **100% failure** | $0 | — | 0 — **100% failure** | $0 | — | not judged (nothing generated) | — |

qwen's task (a) ran as two invocations (an initial run killed mid-flight by an
operator-side timeout at 40 calls/$0.525, resumed to completion/stop at 68 more
calls/$1.0074 — combined 108 calls, $1.5325) and task (b) was stopped deliberately at
23 calls/$0.2254, well under its $0.75 cap, once its cost/latency profile was already
clearly established; both are ledger totals from `runs/20260903T145011Z-43e5c25f.log.jsonl`,
`runs/20260903T145915Z-2044cd53.log.jsonl`, and `runs/20260903T150113Z-86c31505.log.jsonl`.

**Cost per rendition.** haiku task (a): $0.741888 / 199 = **$0.00373/call**. gemini task
(a): $0.761650 / 176 = **$0.00433/call** — higher despite a cheaper per-token rate,
because gemini's calls average **4.7x** haiku's output tokens (763.5 vs 163.5) at the
same `reasoning_effort="low"` policy setting; nothing in the request asked for that
length. haiku task (b): $0.768795 / 1,315 accepted sentences = **$0.000585/accepted
sentence**.

### Provider failure modes (recorded, not worked around — SHELF's own recommendation)

* **`deepseek/deepseek-v4-pro` via OpenRouter: 100% failure on every stage, both
  tasks.** Exact error: `pydantic_ai.exceptions.UserError: Native structured output is
  not supported by this model.` This is raised client-side, before any HTTP request is
  sent — pydantic-ai's own OpenRouter model-profile registry marks this model as not
  supporting the `NativeOutput(strict=True)` constrained-decoding mode every stage in
  this pipeline uses. Not a transient rejection: this writer cannot be used anywhere in
  the current pipeline without a different output-constraint mode (e.g. tool-call-based
  output) for this one model, which is out of this pilot's scope. $0 spent.
* **`gemini-3.7-flash`, direct Google API: task (a) succeeded (176 calls, $0.76), task
  (b) failed 100%.** Exact error, on every one of the ~100 attempted calls:
  `stage 'http' failed after 1 attempt(s): non-retryable status 400: status_code: 400,
  model_name: gemini-3.7-flash, body: {'error': {'code': 400, 'message': 'Request
  contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}`. Google's error body
  gives no further detail. Task (a)'s and task (b)'s output schemas differ
  (`DraftRenditionSet` vs `DraftExampleBatch`), and only the more complex, multi-sense
  batch schema of task (b) fails — the likely cause is a schema feature (nesting depth,
  a field shape) the Gemini structured-output translation in this pydantic-ai version
  does not support, not a content or prompt problem. Not investigated further, per the
  pilot's own instruction to record and move on rather than debug a provider integration
  mid-pilot.
* **`qwen/qwen3.5-397b-a17b` via OpenRouter: no hard failures, but severe, recurring
  reasoning-token blowups.** A normal call runs 200-350 output tokens in a few seconds;
  a blown-up call runs 5,500-8,186 output tokens and 30-165 seconds, at `flex` tier with
  `reasoning_effort="low"` — the model is a reasoning MoE that does not fully respect a
  low-effort request, and its hidden reasoning is billed as output tokens like any
  OpenAI reasoning model's. One call failed outright:
  `invalid output: Model token limit (8192) exceeded before any response was generated.
  Increase the max_tokens model setting, or simplify the prompt to result in a shorter
  response that will fit within the limit.` — the retry (feedback-free, since this is a
  transport-class failure) succeeded but itself used 6,495 output tokens. This inflates
  qwen's real cost and latency well above its per-token price makes it look on paper:
  ~30x more expensive than a normal call, and it is not rare — a majority of the calls
  logged at the tail of the renditions run hit it. **Recommendation: cap `max_tokens`
  tighter and treat qwen as reasoning-effort-uncontrollable via OpenRouter's
  `reasoning.effort` field for this model, or avoid it for latency-sensitive sweeps.**
* OpenRouter's own provider routing was visible end-to-end via the new
  `Provenance.provider` field (D-63): qwen's calls in this pilot were served by
  `Phala`, `StreamLake`, `Venice`, and `Parasail` — different physical backends for the
  same nominal model, on OpenRouter's automatic load-balancing, uncontrolled by this
  pipeline. This is itself a source of the cost/latency variance measured above: the
  blowup calls skew toward specific downstream providers in the log, though the sample
  is too small here to say which one reliably.
* No `temperature`/`top_p` rejections and no mandatory-reasoning rejections
  (`"Reasoning is mandatory..."`) were hit by any of the four working arms in this
  pilot — every policy left `temperature` unset and `reasoning_effort` at the router's
  provider-appropriate default, which is exactly what D-63's router change was built to
  make possible without per-provider special-casing at the call site.

### Quality (Opus judge, `opengloss qa`, seed 42, sample 40)

All four judged arms land in **62.8-64.6**, a 1.8-point band on a 0-100 scale with
33-38 entries judged per arm (5-7 entries per arm failed to judge — see below) — well
within what this sample size can resolve. **No arm reads as a quality regression from
luna**; if anything haiku is marginally lower and gemini/qwen marginally higher, but the
gap is not distinguishable from noise at this sample size. The judge's own defect
histogram (`terminology_error`, `awkward_style`, `hallucination`, `missing_content`,
`factual_error`) is dominated by the SAME categories at similar rates across every arm —
the pilot's own change (which model writes the examples) did not shift what kind of
defect the judge finds, only, marginally, how often.

**5-7 of 40 sampled entries per arm failed to judge**, not because of a defect in the
content but because of `HTTP/1.1 529 Overloaded` from the Anthropic API during this
run — the SDK's own client retried a few times and then gave up, and `_RETRYABLE_STATUS`
in `stages.py` does not include 529, so the stage runner did not retry further. This is
a real, minor gap between the codebase's actual retry coverage and this pilot's working
assumption that "the judge's stage runner already retries" 529s — worth a one-line fix
(add 529 to `_RETRYABLE_STATUS`) but not made here, since it is a judge-reliability fix
orthogonal to writer diversity and untested changes should not be smuggled into a pilot
report.

### Attribution: TF-IDF + logistic regression, balanced, 5-fold (SHELF's method)

Attribution accuracy over the four writers that produced attributable, non-canonical
text (luna, haiku, gemini, qwen — deepseek produced none), balanced by undersampling to
the smallest class and evaluated by 5-fold stratified cross-validation:

| | value |
|---|---|
| accuracy | **66.0%** |
| chance (4 classes) | 25.0% |
| n per writer (balanced) | 517 (qwen's non-canonical count, the limiting class) |

**Confusion matrix** (rows = true writer, columns = predicted, order
haiku/gemini/luna/qwen):

| true \ predicted | haiku | gemini | luna | qwen |
|---|---|---|---|---|
| haiku | 294 | 87 | 82 | 54 |
| gemini | 52 | 353 | 24 | 88 |
| luna | 105 | 56 | 328 | 28 |
| qwen | 50 | 60 | 18 | 389 |

**Confusion is real but far from uniform** — luna is most often confused with haiku
(105 of 517 luna items misclassified as haiku), and qwen is the most cleanly separated
writer (389 of 517 correct) — consistent with qwen's own distinctive, leaked-label
vocabulary being an unusually strong tell. **Top discriminating features per writer**
(positive logistic-regression coefficients): haiku's are ordinary connective/function
words ("and", "you", "up", "got", "established") — a genuinely low fingerprint;
gemini's are topical nouns from its particular slice of the 300-entry sample
("caustic", "caged", "circuses", "cecil", "chloride") — this is very likely **topic
leakage from an uneven split, not a style tell**, because each arm's budget stopped at a
different point in the alphabetically-ordered headword list, so the arms cover
different, only partially-overlapping subsets of the 300 entries; qwen's top features
are **`bbq`, `alexa`, `babel`, `grade_10`, `akash`, `college`, `grade_1`, `grade_5`,
`allenby`** — literal reading-level labels and headwords leaking into the generated
sentence text (confirmed directly in its sentence-opener list below: "grade plain the"
appears four times verbatim) — a genuine, fixable style tell, not topic leakage, since
`grade_10`/`college`/`grade_1`/`grade_5` are prompt-structure tokens that appear for
every headword, not this writer's particular topic slice.

**Caveat, stated plainly:** this pilot's attribution number is confounded by the uneven
alphabetical coverage above in a way the SHELF methodology it borrows from explicitly
warns against (Cramér's V between generator and content was checked there; it was not
checked here, for time). A production rollout comparison should draw every arm's sample
from the *same* fixed subset of entries, not let each arm's own budget stop decide which
entries it covers.

### Lexical diversity

| writer | n items | type-token ratio | distinct-4-gram rate | opener entropy (bits) |
|---|---|---|---|---|
| gpt-5.6-luna | 15,518 | 0.068 | 0.8895 | 13.30 |
| claude-haiku-4-5 | 3,073 | 0.194 | 0.9912 | 11.50 |
| gemini-3.7-flash | 554 | 0.367 | 0.9790 | 8.96 |
| qwen/qwen3.5-397b-a17b | 517 | 0.303 | 0.9839 | 8.91 |
| **pooled, all writers** | 19,699 | **0.0634** | **0.9066** | — |
| **pooled, luna only** | 15,518 | 0.0680 | 0.8895 | — |

qwen's top sentence-openers include **"grade plain the"** (4 occurrences) — the same
prompt-label leakage the attribution features caught, visible directly in the raw
opener list rather than only in a model's learned coefficients.

**Read this with the sample-size caveat SHELF's own §6.2 finding warns about**:
type-token ratio and distinct-n-gram rate both mechanically rise as a corpus shrinks
(fewer chances for the same word or 4-gram to repeat), so gemini's and qwen's much
higher TTR against far smaller item counts (554, 517) is not directly comparable to
haiku's (3,073) or luna's (15,518) on this measure alone — the *pooled* comparison
(mix vs. luna-only) is the more trustworthy number here, and it moves only slightly
(TTR 0.0634 vs 0.0680; distinct-4-gram 0.9066 vs 0.8895) because luna's own volume still
dominates the pooled mix at this sample's writer ratios. **A production mix at a more
even split across writers would move the pooled number further than this pilot's
un-even, budget-truncated arms show.**

### Ontology anchoring

| writer | example renditions | headword-present rate | gloss renditions (non-canonical) | headword-initial rate | near-copy rate |
|---|---|---|---|---|---|
| gpt-5.6-luna | 8,545 | 99.27% | 6,973 | 5.13% | 0.40% |
| claude-haiku-4-5 | 3,073 | 98.21% | 0 (task didn't touch gloss) | n/a | n/a |
| gemini-3.7-flash | 554 | 99.64% | 0 | n/a | n/a |
| qwen/qwen3.5-397b-a17b | 517 | 100.0% | 0 | n/a | n/a |

Every writer anchors to the headword at least as well as luna's own baseline; none
shows a meaningful ontology-anchoring regression. Gloss-level headword-initial and
near-copy rates are luna-only here because this pilot's two tasks target the
`EXAMPLES` field and the D-53 examples workflow, not `GLOSS` renditions — a design
choice inherited from the pilot's own task definition, not an oversight, but it means
this pilot has nothing to say about how another writer would perform at gloss rewriting.

### Rejection rate through the existing generation-time gates, with the breakdown

Share of each writer's non-canonical items that still carry a QA flag after their one
retry (FK band -> `og.readability_miss`, headword-initial -> `og.headword_initial`,
headword-absent -> `og.headword_absent`, hard vocabulary -> `og.hard_vocabulary`,
near-copy -> `og.near_copy`; `factual_error`/`audience_inappropriate`/etc. are judge-time
flags from a separate QA pass, included here because they are stored on the same
`Assessment.qa_flags` list):

| writer | n items | any-flag rate | flag breakdown |
|---|---|---|---|
| gpt-5.6-luna | 15,350 | **1.92%** | readability_miss 192 (1.25%), hard_vocabulary 54 (0.35%), factual_error 34 (0.22%), headword_initial 14 (0.09%), headword_absent 6 (0.04%), audience_inappropriate 6 (0.04%), register_mismatch 2 (0.01%) |
| claude-haiku-4-5 | 3,073 | **0.72%** | headword_absent 10 (0.33%), readability_miss 8 (0.26%), hard_vocabulary 3 (0.10%), factual_error 3 (0.10%) |
| gemini-3.7-flash | 554 | **1.62%** | audience_inappropriate 2 (0.36%), hard_vocabulary 2 (0.36%), headword_absent 2 (0.36%), readability_miss 2 (0.36%), factual_error 1 (0.18%) |
| qwen/qwen3.5-397b-a17b | 517 | **1.16%** | factual_error 4 (0.77%), readability_miss 2 (0.39%), hard_vocabulary 1 (0.19%) |

Every writer's any-flag rate is under 2%, and haiku's is the lowest of the four —
consistent with its clean pass through both tasks. luna's own baseline (from its much
larger, pre-pilot production history) is not a floor these writers beat by a wide
margin, but none is worse enough to read as a quality regression at generation time,
which is a separate, and more direct, signal than the judge's 0-100 score above. The
D-53 example-sentence workflow's own rejection counters (task (b), haiku only —
gemini's task (b) never ran and qwen's is too small a sample at 23 calls to be
meaningful) are the other half of "rejection rate": of 1,960 sentences haiku's task (b)
generated, 1,315 were accepted (67.1%) and 473 rejected, broken down as `too_long` 149,
`unwanted` 88, `headword_absent` 58, `readability` 87, `repeated_opening` 44,
`hard_vocabulary` 22, `gloss_shaped` 21, `too_short` 3, `not_a_sentence` 1, plus 132
sentences `refiled_dropped` by the sense-fit check (written for a sense they did not
fit). This is the dominant rejection channel by volume — the free deterministic checks
reject far more than the QA-flag-after-retry mechanism above ever sees, because most
misses are caught and discarded before they are ever stored, let alone flagged.

### Corpus-level filler (`qc filler`, no model calls, whole-store scan)

| writer's store | sentences scanned | flagged candidates | rate |
|---|---|---|---|
| luna | 23,363 | 888 | 3.80% |
| haiku | 18,835 | 790 | 4.19% |
| gemini | 16,053 | 760 | 4.73% |

**Diluted, and said so on purpose**: `qc filler` scans the whole store, not just this
pilot's added content, and haiku/gemini's task (a) only reached 176-199 of 300 entries
before their budget stopped, so most of what each store scans is still luna's original
production content. The small rate differences above are not strong evidence of a
per-writer filler difference; a proper measurement would restrict the scan to only the
sentences this pilot itself generated.

## Three real sentences per writer, one shared sense (`accuser:noun:0`)

| level | luna | haiku | gemini | qwen |
|---|---|---|---|---|
| grade_1 | "The accuser told the judge what happened." | "The accuser told the judge what happened." *(identical to luna — plausible convergence on the simplest sentence the constraint allows, not copying: the reset never carries non-canonical text forward)* | "Ben looked right at his accuser in class." | "The accuser showed some papers. She wanted people to know. The judge looked close." *(three short clauses rather than one sentence; "looked close" instead of "closely")* |
| grade_5 | "The accuser told the court that the thief took her bike." | "The accuser explained to the court why she believed the man had broken the law." | "Maya listened quietly as her accuser claimed she had taken the missing library book." | "The accuser brought important papers to the meeting. She wanted to prove she was right." |
| college | "The accuser submitted text messages that supported the allegation against the defendant." | "The accuser marshalled documentary evidence at trial, establishing a material foundation for the charges." | "The defense counsel aggressively questioned the accuser to expose inconsistencies across successive sworn depositions." | "The accuser submitted documentary evidence to substantiate the allegations during the formal proceedings." |

At `college`, four writers independently reach for "documentary evidence"/"substantiate
the allegations"-class legal register — real, visible convergence on register norms even
while sentence structure differs; at `grade_1`, the constraint space is narrow enough
that luna and haiku produced the identical sentence.

## Reading of the trade-off

* **Diversity is real but this pilot's arms are too small and too uneven to size it
  precisely.** The pooled type-token-ratio and distinct-4-gram movement (0.068 -> 0.064,
  0.890 -> 0.906) is in the right direction but small, because luna's volume still
  dominates the pooled mix at the coverage this pilot's budgets reached. Attribution
  accuracy (66.0% vs. 25% chance, 4 writers) says style is detectable well above chance,
  which is the actual goal (diverse tokens for encoder training benefit from exactly
  this kind of detectable-but-not-dominant stylistic variation) — but the number is
  confounded by uneven topic coverage across arms and should be re-measured on a
  matched subset before being trusted as a target metric.
* **Quality does not measurably suffer.** All four working writers judge within 1.8
  points of each other and of luna on Opus's 0-100 scale, at a sample size too small to
  separate them with confidence, which is itself the useful finding: nothing here
  argues against rotating writers on quality grounds.
* **Two of five candidate writers are not viable with the current pipeline as shipped.**
  `deepseek/deepseek-v4-pro` cannot be called at all (native structured output
  unsupported). `gemini-3.7-flash` works for graded-rendition rewriting but not for the
  D-53 multi-sense example-batch schema. Neither failure is a quality problem; both are
  integration gaps this pilot deliberately did not spend time working around.
* **Cost is writer-dependent in a way per-token pricing does not predict.** gemini's
  verbosity (4.7x haiku's output tokens per call) and qwen's reasoning blowups (up to
  ~30x a normal call) both push real cost well above what the price table alone
  suggests; haiku's cost and behavior were the most predictable of the three working
  non-luna writers.

## Recommendation

**Ship a two-writer rotation for the two tasks this pilot tested — `gpt-5.6-luna`
(majority) and `claude-haiku-4-5` (minority) — via the new `ModelPolicy.writers` /
`writer_for` mechanism, on the `RENDITIONS` and `EXAMPLES` stages only, at a starting
mix weighted toward luna (e.g. 80/20) rather than an even split.** Reasoning:

1. haiku is the only non-luna writer that passed *both* tasks cleanly, at a predictable,
   measured cost ($0.00373/rendition, $0.000585/accepted example sentence) and with no
   quality regression on the judge.
2. gemini is a plausible *second* rotation candidate for task (a) specifically (graded
   renditions), once its D-53 schema failure is understood or the D-53 workflow is
   excluded from its policy — not recommended for both tasks yet.
3. qwen's reasoning-blowup risk makes its cost and latency unpredictable enough that it
   should not join a production rotation until `max_tokens`/reasoning-effort handling
   for this specific OpenRouter endpoint is tightened; its style-tell (leaking prompt
   labels into text) is also a real, visible defect a reader would notice.
4. deepseek is not usable at all without separate integration work.

**Measured cost delta versus luna-only**, at this pilot's own numbers: an 80/20
luna/haiku mix on the two tested stages raises the per-call cost of the 20% share from
whatever luna's own rate is to haiku's measured $0.00373 (task a) / $0.000585-per-
accepted-sentence (task b) rate — both in the same order of magnitude as luna's own
production costs for the same stages, so the aggregate cost impact of a 20% haiku share
is small (a few percent of stage spend) against the diversity gained (haiku's own
non-canonical content measures a 0.99 distinct-4-gram rate against luna's 0.89, and its
style-tell features are unremarkable function words rather than a detectable
fingerprint) — the trade this pilot was built to price out.

## What was not done

* No local vLLM writer: not attempted. Given the wall-clock this pilot's five API-based
  arms already took (real provider latency dominated, not compute), and that no GPU
  environment was already prepared for this branch, standing up a quantized 14B model
  from scratch was judged not to fit in the pilot's time budget. Left for a follow-up
  pilot, specifically to compare a same-family-as-nothing-else local writer's diversity
  contribution against the four hosted writers measured here.
* Attribution was not re-measured on a topic-matched subset (see the confound noted
  above) — the number reported is real and ledger-sourced, but should not be treated as
  the final word on how detectable a production writer rotation's style actually is.
* `qc filler` was not restricted to only this pilot's generated content, so its
  per-writer numbers are diluted by each store's much larger luna-authored baseline.
