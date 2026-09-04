# Core-10K diary

Working log for bringing the foundational 10,000 words to a pristine state. One entry per
iteration: what was found, what was done, what it cost. Newest last.

## Definition of "pristine" (set 2026-09-02, revised as findings demand)

For every entry in `data/core/core_10k.tsv`:

1. `kind` classified (not a migration placeholder), `proper_noun` typed where applicable.
2. Every sense tagged with a taxonomy `domain`.
3. Every canonical example carries a headword `span`.
4. Every relation whose target is in the core is resolved to a `sense_id`.
5. No artifact relations (hypernym-slot labels like "descriptive term").
6. Gloss renditions: 4 reading levels (`grade_1/5/10/college`) × plain, plus 4 registers
   (`informal/formal/technical/marketing`) at neutral level — 8 per sense.
7. Example renditions at the 4 reading levels.
8. Encyclopedia renditions at the 4 reading levels (registers deliberately off — cost).
9. Free consistency checks pass: gloss does not start with the headword, hypernym graph
   acyclic within the core, readability of each rendition within its band.

`audit.py` now measures item 9's graph half directly: hypernym cycles and same-lexeme
self-loops, plus symmetric-relation (`synonym`/`antonym`/`confusable_with`) reciprocity,
both under `as_dict()["graph"]` (D-40).

Running cost total is tracked at the bottom.

## Iteration 0 — 2026-09-02 — setup

- Core list computed with zero model calls (`docs/CORE-10K.md`).
- Migrating the 10,000 v1.3 entries into a dedicated store `data/core-store/`.
- Live API smoke test to confirm flex + structured output work against real models.
- Adding `enrich --from-list` batch mode and an `audit` command to measure the
  definition above; each later iteration starts from an audit.
- Migration: 10,000 / 10,000 entries, 0 failures, ~6 min (NFS read bound).

### Findings from the live smoke test (all fixed before any spend at scale)

1. **`temperature` is rejected by gpt-5.x when reasoning is on.** The encyclopedia
   policy set 0.9 and the stage failed 100% of the time. Removed; no policy sets
   sampling parameters now (`config.py`).
2. **Tool-call structured output does not enforce enums.** `gpt-5.4-nano` filled
   `DraftOverview.kind` with `"verb"` on every attempt (it read the field as part of
   speech). Two fixes: every stage now uses `NativeOutput(..., strict=True)` — the
   provider constrains decoding to the schema — and `kind` carries a description saying
   it is *not* part of speech. Non-strict native mode was also tried: it omitted
   required fields (`examples`). Strict is the only mode that held.
3. **Retry feedback never contained the real error.** pydantic-ai wraps the
   `ValidationError` in "Exceeded maximum output retries (0)"; we were feeding that
   wrapper back to the model. `_validation_detail()` now walks the cause chain.
4. **Reasoning tokens count against `max_tokens` on the Responses API.** A four-sense
   set at medium effort truncated at 4096 (JSON cut at char 4505). Prose stages now get
   8192; only paid when used.
5. After the fixes: all six generate stages complete live with zero retries; cache hit
   rate 34–77% depending on call order; a full `abseil` entry plus two graded glosses
   costs ~$0.002.

### Raw v1.3 quality observed on core entries (`people`, `vow`) — candidates for later iterations

- Glosses frequently begin with the headword ("The word people refers to…", "To vow is
  to…"), violating our own senses rule and pristine check 9.
- Verb hypernym slots hold meta-labels ("transitive verb", "action verb") — these are
  the artifact relations behind the in-degree contamination in `CORE-10K.md`.
- Examples are in a stilted academic register ("Researchers sample people to understand
  algebra readiness") — the paper's QA flagged the same. Reading-level example renditions
  will address the audience side; the canonical examples themselves may need a rewrite pass.
- Encyclopedia prose carries markdown emphasis (`**people**`, `*plural of person*`).

## Iteration 1 — 2026-09-02 — free passes and the rendition pilot

- Running `retrofit` (classify_kind, tag_domain, spans fallback) over all 10,000, cap $2.
- Rendition pilot on 10 words in a scratch store, cap $1: gloss at 4 levels + 4
  registers, examples at 4 levels, encyclopedia at 4 levels. Output is read by hand
  before anything scales.

## Cost ledger

| Iteration | What | USD |
|---|---|---|
| 0 | migration, audit (no model calls) | 0.00 |

### Iteration 1 results

**Rendition pilot** (10 words, scratch store): 184 calls, 736 renditions, **$0.044, 25 s**
→ ~$0.0044/word → ~$44 for the full core at this coverage (8 gloss renditions per sense,
4 example levels, 4 encyclopedia levels). Polysemous words dominate: `round` (12 senses)
cost 148 renditions, `eager` 28.

What is good: reading levels differentiate properly for glosses and encyclopedia
(`vow`, `acceleration` are exemplary); registers differentiate (technical `vow` =
"performative speech act", marketing = "powerful promise"); example spans are correct;
zero validation retries with strict native output.

**Findings to address (iteration 2):**

1. **Headword-initial canonical glosses propagate into every rendition.** `people`'s
   canonical is "The word people refers to…", so all 8 renditions begin "People is/means".
   Needs a canonical-hygiene pass *before* renditions: rewrite glosses that start with
   the headword (nano, only offending senses), and strip markdown from canonical prose.
2. **Grade-1 output is not grade 1.** The grade-1 `acceleration` encyclopedia contains
   `m/s^2` and `a = Δv/Δt`; a grade-1 `people` example has six-year-olds doing algebra.
   The model anchors on source *content* rather than audience. Fix: explicit per-level
   constraints in the instructions (sentence length, no symbols, concrete vocabulary),
   plus a deterministic readability measurement written to `Assessment` with
   regenerate-on-miss for the two lowest levels.
3. **Zero prompt-cache hits** on 177K input tokens: mean input per call ≈ 960 tokens,
   below OpenAI's 1,024-token caching minimum. The richer instructions from (2) push the
   static prefix past the threshold, which turns ~80% of input tokens into cached tokens
   at one-tenth the price. Verify on the next pilot.

Also observed, lower priority: stilted canonical examples ("Researchers must abide by
the vow of confidentiality") shape their renditions; legacy-mapped domain tags are poor
(`people` → `history.general`, `acceleration` → `science.general`) — a retag of
legacy-mapped and `.general` senses is cheap (~$1.4 for the core).

### Baseline audit (mid-retrofit snapshot, `opengloss audit --from-list data/core/core_10k.tsv`)

| Check | Value |
|---|---|
| entries / senses | 10,000 / 38,958 (3.9 senses per word — the core is the polysemous part of the graph) |
| kind classified | 100% (classify_kind pass done; 62–75% deterministic on this data) |
| senses with domain | 82.5% (tag_domain pass still running) |
| canonical examples with span | 94.9% (36,831 / 38,819) |
| relations | 378,800; **159,840 (42%) target another core word**; 0 resolved |
| artifact relations | 2,578 (0.68%) |
| glosses starting with the headword | 1,787 senses (4.6%) |
| renditions (all fields) | 0 |

Full-coverage targets: 311,664 gloss renditions (8 per sense), 155,832 example
renditions (4 per sense), 40,000 encyclopedia renditions (4 per entry). Scaling the
pilot's cost per rendition gives ≈ $30 before caching; with the >1,024-token static
prefix it should land nearer $22.

Plan for iteration 2, in dependency order: hygiene pass (strip markdown, drop artifact
relations, rewrite the 1,787 headword-initial glosses — ~$0.4) → re-tag weak domains
(nano, ~$1.4) → `resolve` over the 159,840 in-core targets (~8,600 nano calls, ~$3) →
re-pilot renditions with the revised instructions, confirm cache hit > 0 and grade-1
readability → scale renditions in budgeted batches.

## Iteration 2 — 2026-09-02 — hygiene, readability, cache

Code landed this iteration (all gates green, 349 tests): `hygiene` retrofit pass
(markdown strip, artifact-relation drop, headword-initial gloss rewrite with the old
gloss kept in `Provenance.note`, weak-domain clearing); per-level constraints and a
worked example in the renditions instructions (static prefix ~1,800 tokens, past the
1,024-token cache floor); `readability.py` with Flesch-Kincaid on every rendition
written to `Assessment`, retry-once for grade_1/grade_5 misses, the headword scored as
one syllable; `audit` and `enrich --from-list/--all` commands.

**Pilot 2** (same 10 words): 206 calls, 736 renditions, **$0.036** (was $0.044),
**cache hit rate 86.8%** (was 0%), retry rate 12%. Grade-1 output is now grade 1:
"People play with blocks on the floor." / "A place where people keep money. It lends
money too." (FK 1.7) / "Mia puts her money in a bank to keep it safe." Examples are
fresh sentences per audience. Remaining defect: renditions of entries whose canonical
begins with the headword still begin with it ("People are human beings."), which is why
hygiene must run before renditions.

**Operational findings:** (1) retrofit at 16 workers was latency-bound on flex
(~19 entries/min); restarted with the new pass order at 64 workers / 1,500 RPM.
(2) The `enrich --dry-run` estimator priced every call at `max_tokens` (8,192) of
output — $436 for a sweep the pilot measures at ~$15. Fixed to use measured means.
Sweep projection from pilot 2: ~$15 (levels: gloss, examples, encyclopedia) + ~$7
(registers: gloss) ≈ **$22**.

Order of operations from here: retrofit-2 (classify_kind → hygiene → tag_domain →
spans) → resolve in-core targets → renditions sweeps → audit.

### Iteration 2, mid-retrofit observations

- Register enum grounded in ISO 12620/TBX DC-423 (D-27, `docs/REGISTERS.md`):
  `professional → formal`; `slang`/`in_house` available; `marketing` kept as a genre
  value. Legacy JSON still loads. Sweep defaults: informal/formal/technical/marketing.
- `docs/STANDARDS-PLAN.md` written: ten fields, three phases, ≤ $1 on the core.
- Hygiene pass, first 500 entries: 851 glosses rewritten. Common nouns come out right
  ("Games are organized activities…" → "Organized activities with rules and goals…").
  **Defect:** proper nouns game the check — "Congo River, a major central African
  river…" → "The Congo River is a major…". Proper-noun definitions legitimately name
  the entity (WordNet does the same), so the fix is to exclude `kind=proper_noun` from
  the rewrite rather than tighten the prompt. Cheap; queued for iteration 3, and the
  superseded text is in `Provenance.note` so nothing is lost either way.

### Iteration 2 — finding 4 (operational, blocking): retrofit passes are sequential

Measured: hygiene advances ~40 entries/min; `tag_domain` 0/min while hygiene runs.
`retrofit.py`'s four passes are each a `for lexeme_id in ids:` loop awaiting one model
call at a time — the configured 64 workers never applied. After hygiene cleared ~15K
`.general` senses, `tag_domain` needs a call for nearly every entry; sequentially at
flex latency that is on the order of a day. Fix in progress: run each pass's per-entry
work through the bounded pool (`runner.run_pool`) under `store.locked`, keeping the
idempotence markers so the running job can be killed and relaunched without loss.

**Fixed (D-31).** Each pass now builds its id list and runs it through `run_pool` at the
configured worker count, with the handler holding `store.locked(lexeme_id)` across the
whole of read → deterministic work → model call → write. That fixes the throughput and, in
the same move, a lost-update hazard the same reading turned up: `hygiene`, `tag_domain` and
`spans` had been reading the entry outside the lock and writing it inside. `classify_kind`'s
residue batch is the one place the lock cannot span the call — it decides 50 entries at
once — so it re-reads each entry under that entry's lock and applies the verdict there.
Per-pass counters moved into a lock-guarded `_Tally`; a budget stop is now reported as
`stopped_reason="budget"` on the pass and the outcome is returned rather than raised, so
later passes are skipped and a partial run still reports what it did. `resolve_store` got
the same pool treatment. The idempotence markers are unchanged, so the job running against
`data/core-store` can simply be stopped and relaunched on the new code. Long passes log
`retrofit_pass_progress` every 500 entries so the rate is visible from the log file.

**Fix landed (D-31):** every retrofit pass and `resolve_store` now run per-entry work
through the bounded pool with the lock held across read → model call → write; counters
are lock-guarded; a budget stop is reported (`stopped_reason`) rather than raised so
later passes are skipped cleanly. Retrofit-3 on the same store: **~1,600 entry
writes/min vs ~40** before, zero failed attempts. Also this iteration: Phase A3/A5 of
the standards plan (Zipf fields, `concept_id` format), proper nouns exempt from the
headword-initial rewrite (D-30), `docs/STANDARDS.md` (10 fields, every list cited) and
the plan reconciled to its verdicts. Tests: 431.

## Iteration 3 — 2026-09-02 — retrofit done, resolve + rendition sweeps

**Retrofit-3 (parallel):** $1.53, 559 s, cache hit 84%, no flex downgrade.
tag_domain re-tagged 9,114 entries / 34,621 senses (nano, $1.26); spans filled 3,503
(1,097 free, 2,406 by model, $0.26); hygiene rewrote the remaining headword-initial
glosses and dropped 3,076 artifact relations.

**Audit after retrofit, before renditions:**

| Check | Before | After |
|---|---|---|
| senses with domain | 82.5% | **99.99%** |
| canonical examples with span | 94.9% | **99.4%** |
| artifact relations | 2,578 | **0** |
| glosses starting with headword | 1,787 | **36** (proper nouns, exempt by D-30) |
| duplicate canonical gloss (entries) | 3 | 3 |
| entries with zero examples | 12 | 12 |

Launched, concurrently (per-entry locks make this safe; separate models, separate
rate limits): `resolve --all` (cap $5), sweep 1 = gloss/examples/encyclopedia at four
reading levels (cap $25), sweep 2 = gloss at four registers (cap $12).

Findings to address next (iteration 4), in order: (1) the 12 entries with zero
examples and 3 with duplicate glosses — tiny, fix by a targeted `generate`-style
regeneration of the affected senses; (2) whatever the readability-miss flag count says
after the sweep — that is the first real measure of grade-1/grade-5 quality at scale;
(3) the one entry retrofit scanned as 9,999 of 10,000 — find it.

**Sweep 1 relaunched at 128 workers.** First run measured $0.003/entry (→ ~$30 for the
core, above the $25 cap) and ~55 entries/min at 48 workers — flex latency, not our
limiter (the env override was confirmed to reach the config). Killed after 215 entries
and relaunched at 128 workers, cap $32. Two operational lessons recorded for the
runbook: (1) kill by PID, never by `pkill -f` on a string that also appears in the
killing shell's own command line; (2) a killed worker leaves its entry lock in place,
and `_break_if_stale` only breaks locks older than the 900 s TTL — so after any hard
stop, remove locks whose owner PID is dead before relaunching (49 cleared here), or the
relaunch times out on exactly those entries. Worth making `_break_if_stale` check PID
liveness *before* the TTL for locks on the same host (queued for iteration 4).

Measured after relaunch: sweep 1 ≈ 76 entries/min at 128 workers (latency-bound; the
gain over 48 workers is modest because each entry's retry chain is serial), ≈
$0.0027/entry → ≈ 2 h and ≈ $27 remaining; sweep 2 ≈ 130 entries/min, ≈ $0.0006/entry.
Store fix landed meanwhile (433 tests): a dead-owner lock on the same host is broken
immediately, hostname recorded in the lock file, other-host locks keep the TTL.

**Resolve (run 1, stopped on its $5 cap after 11 min):** 108,404 relations resolved,
6,137 declined, mean confidence 0.86, 7,474 calls, zero errors. **Finding (iteration
4, #1):** $0.00066/call is 2.5× the cost model because (a) cache hit rate was 0.15% —
`RESOLVE_INSTRUCTIONS` is under the 1,024-token cache floor, the same defect the
renditions prompt had in iteration 1 — and (b) output averaged 660 tokens per call
(verbose contract plus reasoning). Fix in progress: lengthen the static block with
decision rules and a worked example, drop free-text output, use the cheapest accepted
`reasoning_effort` for every nano classification stage. The remainder of the core
(~2,500 entries) resolves on the fixed prompt.

**Code landed this iteration (D-37, 442 tests):** a fifth retrofit pass, `repair`,
appended to `RetrofitPass.ALL` after `spans` to close the first two items this
iteration's own findings queued. Step (a), free: retire the later of any two non-retired
senses in an entry whose canonical gloss is identical once case, whitespace, and a
trailing period are normalised away — never deleting or renumbering (D-1) — which is
what the 3 duplicate-gloss entries the audit above found need. Step (b), one nano call
per entry: every non-retired sense a first pass left with zero canonical examples gets
one or two natural sentences written for it, shown the entry's other senses for context
so the model can tell them apart, with the span found the same way the `spans` pass finds
one and left `None` for that pass to retry when it cannot — which is what the 12
zero-example entries need. Reuses `StageName.HYGIENE`'s model policy rather than adding a
stage for one call site.

## Iteration 4 — 2026-09-02 — cost of classification stages, repair pass

**Finding resolved (D-38):** the resolve stage's real cost driver was not the prompt
shape but *hidden reasoning tokens billed as output* — ~620 of the ~660 output tokens
per call. `gpt-5.4-nano` accepts `reasoning_effort="none"`; with that plus a static
instruction block past the cache floor (1,117 → 7,341 chars, with decision rules and a
worked example), a live re-measurement gives cache hit 68%, ~36 output tokens per
call, **$0.000074/call in steady state — 9× cheaper**. Applied to every nano
classification stage (resolve, classify_kind, tag_domain, spans, frontier); hygiene
keeps `low` because it writes prose. Lesson for the cost model: on reasoning models,
measure output tokens per call before believing any estimate — the contract's field
count was never the problem.

Also landed: `repair` retrofit pass (D-37): retire exact-duplicate senses (free,
tombstone only) and generate canonical examples for senses that have none (one nano
call per entry, spans found deterministically). Tests: 449.

Running now against the core: resolve remainder (cap $2, on the fixed prompt) and the
repair pass (cap $1), alongside the two rendition sweeps.

**Resolve remainder (fixed prompt):** +46,339 resolved, 3,877 declined, 5,002 calls,
$1.41, 4.4 min, cache hit 54%. Cumulative: ~154.7K of the 159.8K in-core targets
handled (≈145K resolved, ≈10K declined), mean confidence 0.84.
**Repair pass:** 3 duplicate senses retired; 265 canonical examples added across 63
entries (the audit's "12 entries with zero examples" undercounted: 63 entries had at
least one sense with none); $0.006. Scanned 9,910/10,000 — the 90 skipped were locked
by sweep workers for longer than the 30 s lock timeout; default raised to 300 s, and
the idempotent re-run picks them up. Entries that gained examples after sweep 1 passed
them need a final sweep-1 re-run for their example renditions (only missing targets;
cents).

**Sweep 2 (gloss × informal/formal/technical/marketing) complete:** 153,363
renditions on 9,869 entries, 38,353 calls, **$5.12** (projection was $7), cache hit
91%, 45 min at 32 workers. 129 entries failed on 30 s lock timeouts against sweep-1
workers holding their locks through encyclopedia calls — the cause of the
`lock_timeout_seconds` change above; an idempotent rerun (cap $3) is collecting them.
Sweep 1 is running as two processes over the two halves of the list (~95 entries/min
combined, no throttling observed).

**Quality snapshot mid-sweep (400 swept entries, read-only):** Flesch-Kincaid medians
by level — gloss/examples: grade_1 1.6 (p90 3.8), grade_5 4.8, grade_10 7.8, college
11.7; encyclopedia: grade_1 3.4 (p90 4.4), grade_5 7.2, grade_10 12.6, college 15.5.
Readability-miss flags after the one retry: 4.2% of grade_1 glosses/examples, 6.5% of
grade_1 and 8% of grade_5 encyclopedia renditions. Content reads right for the
audience ("Sam ate a single cookie after lunch."; "Our park has a ban on riding bikes
inside."). No systematic defect that would justify stopping the sweep.

**Finding (iteration 5, #1): headword-initial renditions.** On the same 400 swept
entries (proper nouns excluded): canonical glosses 2.7% headword-initial, but
renditions 10.2% (grade_1), 15.4% (grade_5), 15.3% (grade_10), 13.9% (college), and
~10.5% for every register. The instruction is not enough; the short-sentence targets
pull the model toward "X is …". Fix in progress: treat it as a miss at generation time
(one combined retry with readability, then flag `og.headword_initial`), and a
`rendition_hygiene` retrofit pass that rewrites the ~55K already-stored offenders with
one nano call per entry (≈ $2).

**Fix landed for finding 1 (D-39, 486 tests).** The headword-initial check is now applied
in the three places that needed it, from one shared detector
(`hygiene.is_headword_initial`, which absorbed the `hygiene` pass's private
`_gloss_offends` and added the two shapes the swept sample showed it was missing: a plural
`-s`, and a leading article, as in "A ban is an order to stop."). At generation time
`workflows/enrich.py` treats a headword-initial gloss rendition of a common word as a miss
exactly like a readability miss — the failing targets re-requested once, the non-initial
candidate kept, both calls priced, `rendition_headword_initial` logged with its fix rate,
and `og.headword_initial` set on whatever survives — and the two checks share that one
retry, so a rendition failing both is re-requested once with both notes rather than twice.
For the ~55K renditions already on disk there is a sixth retrofit pass,
`rendition_hygiene`, after `repair`: one nano call per entry listing every offender with
the reading level and register it must hold, rewrites applied and re-measured, the
superseded text kept in `Provenance.note`, ≈ **$2** projected over the core. `audit`
gained `gloss_renditions_headword_initial` (count and % over non-canonical gloss
renditions, proper nouns excluded), which is the before/after this iteration will report:
the swept-sample baseline is 10-15% depending on target, against 2.7% for the canonical
glosses the `hygiene` pass has already been over.

**Sweep 1c stopped early (finding, iteration 5 #2):** `--budget 8` at 128 workers
stopped with `stop_reason=budget` at **$4.96 spent** (1,641 entries, 50,002 renditions,
cache hit 84%, zero failures). The budget guard reserves each in-flight call at
`max_tokens` of output — 8,192 for renditions against a measured ~250 — so 128 workers
held ~$3 of phantom reservations. Coverage is unaffected (sweep 1b's range includes
1c's), but any large run would stop at ~60% of its cap. Fix in progress: reserve at a
per-stage `expected_output_tokens` from the measurements, keep `max_tokens` only for
the rate limiter's token reservation, and log refused reservations.

**Fix landed for finding 2 (D-41, 496 tests).** `ModelPolicy.expected_output_tokens`
now carries the per-stage measured figures above (renditions 400, senses 600,
encyclopedia 1600, etc.), and `StageRunner._attempt` reserves the budget guard at that
number instead of `max_tokens`; the rate limiter's own reservation is untouched, since
over-reserving TPM only costs throughput while under-reserving it risks a 429.
`BudgetGuard.reserve` now logs `budget_reservation_refused` with the committed,
reserved, estimate, and ceiling figures on every refusal, so the next early stop is
read off the log rather than reconstructed from the summary. A scenario test
reproduces sweep 1c's shape (128 concurrent RENDITIONS-shaped reservations against a
near-exhausted $8 ceiling) and confirms it now admits all 128, where the pre-fix
formula refused some of them.

## Iteration 5 — 2026-09-02 — sweeps complete, closing chain

**Sweep 1 (gloss + examples + encyclopedia × grade_1/5/10/college), all processes:**
340,019 renditions; 1a $16.56 (5,556 entries, 151 lock-timeout failures under the old
30 s default), 1b $7.64 (2,560), 1c $4.96 (1,641, stopped early by the reservation
defect), plus $1.22 from the killed first run → **≈ $30.4**; cache hit 84–85%; no
provider throttling at up to three concurrent luna processes.
**Sweep 2 (gloss × 4 registers):** 155,832 renditions, $5.21 incl. the rerun (169
entries recovered, 3 still failed).

**Cumulative core spend ≈ $44** (retrofit 1.53, resolve 6.37, repair 0.01, sweeps
35.6, pilots 0.13).

Closing chain launched: `retrofit --only all` (idempotent catch-up — the 3 untagged
senses, the 90 lock-skipped repairs — plus the new `rendition_hygiene` rewrite of
headword-initial renditions), then final sweep-1 and sweep-2 passes for the entries
that gained examples or failed on locks, then the audit against the full pristine
definition including the new graph checks.

**`readability_hygiene` (D-42), added after this chain was launched.** Pristine check 9
also asks that every rendition's readability sit inside its band, and the generation-time
check (`workflows/enrich.py`) only ever gets one retry at `grade_1`/`grade_5`, so the
~4-8% that still miss after it sit on disk carrying `QAFlag.OG_READABILITY_MISS` with
nothing revisiting them — the same gap `rendition_hygiene` (D-39) closed for the
headword-initial defect, on the same renditions, for a different defect. A seventh pass,
appended after `rendition_hygiene` for the reason its own docstring gives (it is the more
expensive of the two rendition-reading passes and must not spend fixing text
`rendition_hygiene` is about to rewrite anyway): one call per entry — split in two past a
~3,000-word flagged set, which only an encyclopedia-heavy entry ever reaches — asking for
a rewrite that holds its meaning, register and band, keeping the better of old and new by
grade and re-finding an example's span before trusting it. This pass did not exist when
the closing chain above was launched, so it caught none of this run's renditions; at an
assumed ~25K flagged renditions over ~8K entries, one luna call per entry at ~$0.0004,
the next sweep's bill for it is on the order of **$3**. Detail in D-42.

## Iteration 6 — 2026-09-02 — 100% rendition coverage; the graph and the taxonomy

**Closing chain:** `retrofit --only all` $1.60 / 7.7 min — rendition_hygiene rewrote
29,150 headword-initial renditions (4,546 still initial after the nano round); hygiene
caught 729 more canonical offenders under the broadened article-led detector; spans
placed 2,620 new examples by model; tag_domain re-tagged 11,491 senses that hygiene had
cleared. Final sweep passes: 145 renditions for the 19 entries that gained examples,
then $0.

**Audit — coverage (the goal's core metric):**

| Field | Targets | Coverage |
|---|---|---|
| gloss | neutral/plain + grade_1/5/10/college + informal/formal/technical/marketing | **100% × 9** on 38,955 senses |
| examples | neutral + 4 levels | **100%** on 38,955 senses |
| encyclopedia | neutral + 4 levels | **100%** on 10,000 entries |
| kind / domain / spans / in-core resolution | | 100% / 100% / 99.4% / 97.1% (rest declined by the resolver) |

**Remaining defects, ranked:**
1. **The hypernym graph is not a hierarchy.** On resolved edges: 458 mutual pairs
   (`resource ↔ supply`, `explanation ↔ reasoning` — siblings the model could not
   order, mean confidence 0.87 both ways), 40 self-loops, and one tangled SCC of 2,840
   senses. Reciprocity: synonym 24%, antonym 13%. Fix in progress (deterministic, $0):
   self-loops and cycle-breaking back-edges demoted to `see_also`, mutual hypernyms to
   `synonym`, symmetric relations completed with provenance.
2. **21% of senses on a `.general` leaf** (`everyday_life.general` 3,372) — the
   taxonomy lacks homes for abstract qualities, actions, quantity, time, emotion,
   communication. Fix in progress: sample-driven leaves, versioned taxonomy, and a
   hygiene step (d) that stops clearing every `.general` on every run (a $0.6 loop).
3. 1.6% of renditions still headword-initial after nano; 8,144 (1.6%) still
   readability-flagged. A luna round for the stubborn ones once the readability pass
   lands.
**Correction — finding #4, larger than the audit showed:** the audit's span metric
counts canonical examples only. Across all levels, **2,921 example renditions have no
span, and 2,575 of them contain no form of the headword at all** — the model wrote
around the word ("custody" → "The judge let both parents care for their child.";
"properties" → "Dad owns two houses near our school."), worst at grade_1 (820) and in
the v1.3 canonicals (991). An example that does not use the word is defective. Fix in
progress: a headword-present check joined into the generation-time retry, and an
`example_hygiene` pass (luna, one call per affected entry, ≈ $0.6) that regenerates the
offenders. Two entries (`von`, `rand`) still have zero examples.

**Fix landed (D-45).** Both halves of the correction above are implemented. Generation
time: an `examples` rendition whose text uses no form of its own headword shares D-39's
single combined retry, with feedback from the new
`prompts.build_headword_absent_feedback`, and what is still absent afterwards carries the
new `QAFlag.OG_HEADWORD_ABSENT` — no proper-noun exemption, since an example has to use
its headword whatever the entry is. On disk: the new `workflows/example_hygiene.py`
module's `run_example_hygiene` visits every entry once, lists every example (any level,
canonical included) whose span is `None` and whose text truly contains no headword form,
and asks for one replacement per offender in a single luna call, verifying each reply
with `find_span` before adopting it — exactly `readability_hygiene`'s "verify, don't
trust" discipline. It is a new, self-contained module rather than a `retrofit.py` pass
because two other passes were landing in that file concurrently; wiring
`retrofit --only example_hygiene` in is left for the next pass over
`retrofit.py`/`cli.py`. Not yet run against the core store — offline tests only so far.

**Fix landed (D-43) — defect #1, the hypernym graph.** `workflows/graph_hygiene.py`'s
`run_graph_hygiene` repairs all three tangles and completes the reciprocity, with **no
model call anywhere** — the whole plan is a function of relation types, resolved sense
ids and confidences already on disk, so it costs $0 however often it runs. It reads the
store once into a relation-only projection, builds `audit.py`'s exact hypernym
projection (a `hypernym` as asserted, a `hyponym` reversed, unresolved targets ignored),
and then, in order: demotes every same-lexeme edge to `see_also`; turns each mutual pair
into a `synonym` pair, keeping the confidence, since a mutual hypernym claim at 0.87 both
ways is two siblings, not a hierarchy; breaks every remaining cycle by offering each
strongly connected component's internal edges *best-first* to an incrementally maintained
topological order and demoting to `see_also` the ones that would close a cycle — the
greedy minimum feedback arc set, at 0.09 s and 1,708 edges on a synthetic 2,840-node
component where the naive "find a cycle, remove its worst edge, repeat" form took 32 s
and removed 7,084; and finally writes the missing reverse of every one-sided `synonym`,
`antonym` and `confusable_with`, on the target's own sense, already resolved back at the
source, under a zero-cost `rule:reciprocity` provenance record. Nothing is deleted — a
defective hypernym is demoted, never removed, and its `note` says why (D-1's spirit) —
and because the demoted types are outside the hypernym projection, a second sweep finds
nothing to do. `dry_run=True` reports the whole plan without writing. The outcome counts
self-loops, mutual demotions, cycle edges and components broken per size bucket, and
reciprocals added per type. As with `example_hygiene`, this is a self-contained module
rather than a `retrofit.py` pass because other passes were landing in that file
concurrently; the CLI wiring is left for the next pass over `retrofit.py`/`cli.py`. Not
yet run against the core store — offline `tmp_path` tests only so far.

Cumulative core spend ≈ **$46**.

**Graph hygiene applied (D-43, `opengloss graph-hygiene`):** 33 s, $0, 6,912 entries
changed. Self-loops demoted 40; mutual hypernym pairs → synonym 920; cycle-breaking
demotions 372 of 58,292 hypernym edges (333 of them inside the one 2,840-sense
component — Pearce–Kelly incremental topological ordering, best edge first, removes
~4× fewer edges than naive greedy and runs in 0.09 s on that component); reciprocals
added: synonym 41,232, antonym 27,423. Audit after: **hypernym cycles 0, self-loops 0,
synonym and antonym reciprocity 100%**. Nothing deleted — every demoted edge survives
as `see_also` with a note.
Also landed: taxonomy v2 (D-44, 10 leaves from a read-only sample of `.general`
senses) and the versioned step-(d) rule (D-46) so `.general` is retagged once per
taxonomy version rather than every sweep; `readability_hygiene` pass (D-42).

## Iteration 7 — 2026-09-02 — closing audit

Retrofit chain (taxonomy v2 retag + readability rewrite + idempotent passes): $2.25,
15 min. Example hygiene: 2,392 examples regenerated with the headword present and
spans found, 462 still absent, $0.13, 81 s.

**Audit against the pristine definition (10,000 entries, 38,955 senses):**

| Check | Result |
|---|---|
| kind classified | 100% |
| senses with a domain tag | 100%; `.general` share **21.0% → 8.3%** under taxonomy v2 |
| canonical examples with a span | 99.98% |
| in-core relations resolved | 98.0% (223,226 / 227,813; the remainder declined by the resolver) |
| artifact relations | 0 |
| gloss renditions (9 per sense) | 100% — 311,640 |
| example renditions (4 levels per sense) | 100% |
| encyclopedia renditions (4 levels per entry) | 100% |
| headword-initial canonical glosses | 36 (proper nouns, exempt by D-30) |
| hypernym cycles / self-loops | 0 / 0 |
| synonym / antonym reciprocity | 99.96% / 99.99% |
| duplicate canonical glosses | 0 |
| readability-flagged renditions | 1,559 (0.3%) |

**Residuals, honestly:** 6,480 gloss renditions (2.2%) begin with the headword — *up*
from 4,546, because the readability rewrite pass produced "X is …" forms after the
headword-initial pass had already stamped those entries (finding #1 of this iteration; fixed
below, D-47). 462 examples still avoid the headword; 2 entries (`von`, `rand`) have no
examples; 1,559 renditions remain outside their readability band after two attempts.

**Finding #1, fixed (D-47).** The headword-initial regression was two defects
compounding, not one. `readability_hygiene` ran last, and the simplest way to say a hard
definition is the way a dictionary must not — asked only to lower a grade, the model
answers "A ban is an order to stop." — while `rendition_hygiene`'s per-entry boolean
marker meant the entries it had already stamped were never looked at again. Fixed by
reordering the two passes (the pass that *rewrites* prose now runs before the pass that
*checks the form* of stored prose, so `rendition_hygiene` is last of all), by giving the
readability pass `RENDITIONS_INSTRUCTIONS`' own headword-initial sentence and refusing any
gloss rewrite that still opens with the headword — old text and readability flag kept —
and by keying both passes' markers on a hash of the rendition ids they answered for, so a
changed offending set earns one more attempt (bounded at two per entry). The ~6.5K entries
carrying the old boolean marker each get exactly one more attempt on the next chain, ≈
$1.8; steady state stays $0 per clean sweep.

**Cumulative core spend ≈ $48.** Renditions $35.1, resolve $6.4, retrofit passes
$5.4, graph hygiene $0, repair/examples $0.15, pilots $0.13.

## Close-out — 2026-09-02

Final passes after the pass-interaction fix (D-47): readability_hygiene one more
attempt on 848 flagged renditions (676 now in band, $0.23); rendition_hygiene on the
re-scanned offending set (3,164 rewritten, $0.22).

**Final audit, 10,000 entries / 38,955 senses:**

| Pristine-definition item | Result |
|---|---|
| 1 kind classified, proper nouns typed | 100% (entity_type still `other` — retag is a queued cent-level pass) |
| 2 every sense has a taxonomy domain | 100%; `.general` share 8.3% (was 21%) |
| 3 canonical example spans | 99.98% |
| 4 in-core relations resolved | 98.0%; remainder explicitly declined |
| 5 artifact relations | 0 |
| 6 gloss renditions — 4 levels + 4 registers | **100% (311,640)** |
| 7 example renditions — 4 levels | **100%** |
| 8 encyclopedia renditions — 4 levels | **100%** |
| 9 headword-initial canonicals / acyclic hypernymy / readability | 36 (proper nouns, exempt) / 0 cycles / 778 flagged (0.15%) |
| reciprocity (added to the definition, D-40) | synonym 99.96%, antonym 99.99% |

**Residuals** (all flagged in the data, none silent): 3,245 gloss renditions (1.1%)
still begin with the headword after two nano attempts — a luna round would likely
clear most, ≈ $0.5; 778 renditions outside their readability band after two
attempts; 462 example renditions that avoid the headword; 2 entries (`von`, `rand`)
with no examples; `entity_type=other` on all 1,043 proper nouns (STANDARDS-PLAN B2,
≈ $0.3); `zipf` unset pending the corpus size; etymology `language_code` unset (A2).

**Cumulative core spend: ≈ $48.7.** Renditions $35.1 · resolve $6.4 · retrofit passes
$6.3 · graph $0 · examples/repair $0.15 · pilots $0.13.

**What the iterations taught, in order of cost impact:** hidden reasoning tokens were
the dominant cost of every nano stage (D-38, 9×); a static prefix under 1,024 tokens
means no cache at all (iterations 1 and 4); passes must hold the entry lock across the
model call and must run through a pool (D-31, 40×); the budget guard must reserve at
expected, not maximum, output (D-41); and two passes that both rewrite prose must be
ordered and marker-keyed on the offending set, not on the entry (D-47). Tests: 556.

## Iteration 8 — content hygiene

A QA/QC scan of the 10K core turned up six defects that neither the graph checks nor the
rendition-form checks can see, because none of them is about shape or form: 9,873 senses
assert both `synonym` and `hypernym` at the same target (7,527 with both sides resolved),
185 name their own lexeme a synonym, 63 assert both `synonym` and `antonym`, 5,401
canonical examples are written in the stilted academic register the paper's own v1.3 QA
flagged ("Two researchers formed a duo to complete the project."), 592 senses carry two
rendition targets with identical text and 87 non-canonical renditions are copies of their
canonical gloss, and 21 examples are not sentences at all (`hypernyms([`, `?`, bare single
words). `workflows/content_hygiene.py` (D-49) repairs all six in six selectable,
idempotent steps. Three are free — self-synonyms and contradicted antonyms are demoted to
`see_also` with the reason in `Relation.note`, garbage examples are removed with their
text preserved in a zero-cost provenance note, and a proper noun's synonym/hypernym pair
is settled by rule (`instance_of`, no call). Three cost one call per entry: the
synonym/hypernym direction is genuinely mixed — `tahoe`/`lake` should be neither,
`teach`/`instruct` is synonym, `chief`/`title` is hypernym — so nano is shown both glosses
and asked which holds; luna rewrites the stilted examples (kept only when `find_span` can
still place the headword) and the degenerate renditions (kept only when the result is not
headword-initial and actually differs from the canonical and its siblings). Expected one
sweep: ≈ $0.5 + $1.5 + $0.2 ≈ **$2.2**, one-off, with the offending-set marker of D-47
keeping steady state at $0. Nothing was run against `data/core-store` for this iteration;
these are projections from the scan's counts. Tests: 556 → 594.

## Iteration 9 — vocabulary

The QA judge (`docs/QA-DIARY.md`, iteration 1) found the one defect no deterministic check
in the pipeline could see: 46.6% of grade_1 encyclopedia renditions are not
level-appropriate *while passing their Flesch-Kincaid band*, because FK measures sentence
and syllable length and not whether the reader knows the words. D-51 adds the missing
metric — the share of a text's words that are not on the Dale-Chall familiar-word list
(`vocabulary.py`, `data/easy_words.txt`, 2,947 entries) — measures it on every rendition at
every level into `Assessment.hard_word_share`, acts on it at `grade_1` (0.10) and `grade_5`
(0.25) through `enrich.py`'s existing single retry, and repairs what is on disk with
`workflows/vocabulary_hygiene.py`. Measured read-only over 300 random core entries (seed
51), the share of renditions above the bare band / above band + 0.05 tolerance:
encyclopedia grade_1 **33.7% / 4.3%**, example grade_1 30.1% / 10.7%, gloss grade_1 15.1% /
6.0%, encyclopedia grade_5 26.7% / 7.0%, gloss grade_5 7.9% / 4.1% — the first number is
the free corroboration of the judge's 46.6%. 53.3% of entries carry at least one offender
at the acting threshold (1.9 each, ~62 words of source), so one full sweep of the retrofit
pass is ≈ 5,300 luna calls ≈ **$1.2**, and $0 thereafter by D-47's marker. Nothing was run
against `data/core-store`; the numbers above are measurements of the stored text, not
changes to it. Tests: +61.


## Tier 2 — 2026-09-02 — enriching ranks 10,001–50,000

Goal: enrich the next 40K ranks of the same composite ranking, for pretraining an
embedding model or small LM. Plan and rationale:

- **Cut**: same ranking as the core (`scripts/core_lexicon.py`), ranks ≤ 50,000, minus
  the core, minus junk the core taught us about (≤2-letter tokens, abbreviations,
  function words), and **lemma-folded**: a surface form that is an inflection of a
  higher-ranked candidate does not consume a slot (the core had `organized`,
  `handled`, `builds` as separate entries).
- **Priority order, by what a small encoder trains on** (the embedding lab's data plan:
  sense-centred neighbourhoods, definitions view beneficial, encyclopedia a mixed
  trade): migrate → free retrofit passes → resolve → gloss renditions at all 8 targets →
  example renditions at 4 levels → content/relation/graph hygiene → encyclopedia
  renditions last (reading levels only), so a budget stop loses the least valuable
  material first.
- **Budget discipline**: each stage capped; luna stages serial; the flex→auto downgrade
  active; measured core rates as the only estimate (≈ $83 per 10K all-in).

**Cut:** ranks ≤ 50,000 → 31,886 lemmas after excluding the core, folding 8,071
inflections onto their base forms, and dropping 43 junk tokens; 91,951 senses (2.7×
the core). **Migrated** all 31,886 from the v1.3 working store in 146 s with a
concurrent NFS reader (the core's sequential migration took ~6 min for 10K), 0
failures; the store now holds 41,886 entries. The 18-stage enrichment chain is
running, ordered so a budget stop loses encyclopedia renditions first and typed
relations / sense structure last.

**Stage 1 — retrofit passes over the 31,886 new entries:** $4.70, 35 min, 37,951
calls, cache hit 79%, no flex downgrade, zero failures. classify_kind needed the model
for only 558 entries (the rest by rule); hygiene changed 108,766 items on 31,919
entries (markdown, artifact relations, headword-initial glosses, weak domains);
tag_domain and spans ran for the whole tier. Per-entry cost $0.00015 — identical to the
core's rate, so the estimate scales linearly.

**Stage 2 — resolve:** 447,647 relations resolved to a specific sense, 21,716 declined,
38,780 calls, $12.00 (stopped on its cap), 35 min, cache hit 53%. With 41,886 entries
in the store the share of relation targets that exist in-store is far higher than the
core's 42%, so the tier resolves more edges per entry; a remainder pass is inserted
after graph hygiene (idempotent). Per resolved edge: $0.000027.

## Examples, generated rather than repaired — 2026-09-02

The two example dimensions the judge keeps failing — naturalness and sense fit — never moved under
any repair pass, and iteration 3's stilted-example rewrite made the first one *worse* by swapping
one template for another. `workflows/examples.py` (D-53) takes iteration 4's own conclusion at its
word and regenerates instead: one luna call per entry writes eight fresh sentences for **every**
live sense at once, each tagged with a `(reading level, register)` target cycled from the two
configured axes, and nothing is stored unless it passes a deterministic sieve (uses the headword,
inside its word band, inside its Flesch-Kincaid and Dale-Chall bands, not gloss-shaped, not a
near-duplicate, not another sentence's opening) plus — for a multi-sense entry — one cheap nano
call asking which sense each accepted sentence actually illustrates. Rejects are counted by reason
and not retried: at this volume the next entry's call is cheaper than a retry. Measured live on
`river`, `argue` and `bank` copied out of the core store (nothing written back): 80 sentences
asked, **74 accepted**, 5 rejected (2 too short, 1 headword-absent, 1 repeated opening, 1 hard
vocabulary), 1 dropped by the sense check, for **$0.00338** — $0.0011 per entry, $0.000046 per
accepted sentence. A full core sweep is therefore ≈ $11 for ≈ 272,000 sense-tagged sentences,
which is the cheapest high-value pretraining text this pipeline has produced by an order of
magnitude; the same sweep over Tier 2's 31,886 entries and 91,951 senses is ≈ $35. Tests: +18.

**New generation stage — per-sense verified examples (D-53):** one luna call per
entry writes 8 sentences per live sense across a cycled set of (level, register)
targets, with every sentence accepted only by deterministic checks (headword span,
not gloss-shaped, word band, FK band, Dale–Chall band at grade 1/5, no duplicates,
distinct openings) and, for multi-sense entries, a nano sense-fit verdict that drops
a sentence written for the wrong sense. Live on `bank` (7 senses): 52 of 56 accepted,
$0.0021; **$0.0000457 per accepted sentence**. The sense-fit call now runs at
`reasoning_effort="none"` on its own stage (it was half the cost at `low`). Inserted
into the tier-2 chain before the encyclopedia tail, which was reduced to two levels
(grade 5 + college) as one paraphrase pair — the embedding lab's ablation found
encyclopedia a mixed trade, and encyclopedia renditions were 44% of the core's text.

**Stages 3–5:** graph hygiene over 41,886 entries in 107 s ($0, 23,564 entries changed);
resolve remainder +44,875 resolved / 13,444 declined ($1.85, 8 min, cache hit 73%);
second graph pass 54 s. Tier-2 resolution total: **492,522 relations resolved for
$13.85**. Now in the first rendition stage (glosses at 4 reading levels, ~92K senses).
Tier-2 spend so far: $20.40.

**Throughput note (gloss levels, 48 workers, one luna process):** ~180 entries/min at
$0.001/entry, ~700 luna calls/min, zero 429s. The earlier ceiling was hit at three
processes × 64 workers; one process at 48 is safely under it, so concurrency is left
alone — a 429 storm costs more wall-clock than the headroom would save. The four
rendition stages will take roughly 3 h each at this rate; the chain runs unattended
and every stage is resumable.

**Mid-stage spot check (300 tier-2 entries, 887 grade_1 glosses already written):**
median FK 2.6 / 5.9 / 10.8 / 14.6 across the four levels; headword-initial
renditions 0.0–0.3% (the core's first sweep produced 10–15% before the generation-time
check existed). The tier is heavier in proper nouns (`merlot`, `larsen`) and their
domain tags look loose (`arts.general`, `personal_names`) — a judge-sample item, not a
stage blocker.

**Stage 6 result — gloss renditions, 4 reading levels (20:27 → 23:09, 2h42m):**
143,421 calls, **$28.82**, 31,883 entries changed, 367,452 renditions added, 0 failures,
0 flex downgrades, cache hit rate 0.89, `stop_reason=completed`. Under the $30 cap.
Tier-2 spend to date ≈ $47.

**Stage 7 started 23:09 — gloss renditions, 4 registers** (informal, formal, technical,
marketing; cap $25).

**Stage 7 result — gloss renditions, 4 registers (23:09 → 00:30, 1h21m):**
102,723 calls, **$16.39**, 31,883 entries changed, 367,513 renditions added, 0 failures,
0 flex downgrades, cache hit rate 0.86, `stop_reason=completed`. Cheaper and faster than
the reading-level stage (fewer calls: the register prompt returns all four registers per
sense in one call more often, and register output is shorter than a grade-1 rewrite).
Tier-2 spend to date ≈ $63.

**Stage 8 started 00:30 — example renditions, 4 reading levels** (cap $35).

**Stage 8 result — example renditions, 4 reading levels (00:30 → 02:25, 1h55m):**
112,667 calls, **$20.14**, 31,883 entries changed, 367,472 renditions added, 0 failures,
0 flex downgrades, cache hit rate 0.90, `stop_reason=completed`. Under the $35 cap.
Tier-2 spend to date ≈ $83. All three luna rendition stages done: every live tier-2
sense now carries 9 gloss renditions and 5 example renditions.

**Stage 9 started 02:25 — content-hygiene** (7 free/nano steps), then relation-,
sense-hygiene, repair, catch-ups.

**Stage 9 result — content-hygiene (02:25 → 02:50, 24m):** 19,071 calls, **$2.18**,
18,028 entries changed, 0 downgrades, completed. Per step:

| step | calls | cost | entries | effect |
|---|---|---|---|---|
| self_synonym | 0 | $0 | 448 | 492 demoted |
| synonym_antonym | 0 | $0 | 302 | 362 demoted |
| garbage_examples | 0 | $0 | 18 | dropped |
| synonym_hypernym | 7,981 | $0.99 | 10,810 | 21,485 demoted, 4,680 retyped |
| stilted_examples | 9,230 | $1.02 | 9,112 | 12,251 rewritten |
| degenerate_renditions | 1,366 | $0.10 | 1,338 | 1,436 rewritten |
| fragment_examples | 494 | $0.08 | 484 | 1,695 rewritten |

Same shape as the core's first sweep (synonym→hypernym demotion is the dominant relation
defect; stilted example prose the dominant text defect). Tier-2 spend ≈ $85.

**Stage 10 started 02:50 — relation-hygiene.**

**Stage 10 result — relation-hygiene (02:50 → 03:35, 46m): stopped on its $12 cap.**
16,149 validity calls, **$12.02**, 36,652 entries changed. Free steps: headword_phrases
demoted 85,715 edges (16,847 entries), inflections demoted 41,898 (12,387), meta_labels
demoted 15,712 (5,339). Model step `validity`: 133,907 demoted, 16,133 retyped across
27,299 entries before the cap. Marker census afterwards: **18,683 tier-2 and 5,718 core
entries carry no validity marker yet** (the core figure means part of the core was
judged before D-47's marker existed, or its ref sets moved under resolve/graph-hygiene).
Measured rate $0.00074/call ⇒ the remainder is ≈ $18. A follow-up script
(`$SP/tier2_followup.sh`) waits for `DONE-TIER2`, then runs relation-hygiene at a $20 cap,
graph-hygiene, and a final audit. Tier-2 spend ≈ $97.

**Stage 11 started 03:35 — sense-hygiene** (cap $10).

**Stage 11 result — sense-hygiene (03:35 → 04:07, 31m): stopped on its $10 cap.**
45,459 calls, **$10.00**, 13,884 entries changed. `distinctness`: 27,334 calls, $5.42,
**15,026 senses retired** (merged near-duplicates) across 11,691 entries.
`example_fit`: 18,125 calls, $4.58, 3,133 examples moved and 4,320 removed across 3,765
entries. Marker census afterwards: distinctness unjudged 4,552 tier-2 + 611 core;
example_fit unjudged 14,083 tier-2 + 1,376 core. Measured $0.0002–0.00025/call ⇒
remainder ≈ $5. Follow-up script extended: sense-hygiene ($8 cap) → repair →
relation-hygiene ($20) → graph-hygiene → example catch-up ($5) → final audit.
Tier-2 spend ≈ $107.

**Stage 12 started 04:07 — repair** (regenerate senses whose gloss/examples the
retirements orphaned), then example catch-up, graph-hygiene, example-hygiene,
vocabulary-hygiene.

**Stage 12 result — repair (04:07 → 04:08, 58s):** 1,101 calls, $0.09, 1,100 entries,
2,708 items regenerated. Completed.

**Stage 13 started 04:08 — example renditions (post-repair catch-up).**

**Stage 13 result — example renditions catch-up (04:08 → 04:13, 4m40s):** 2,770 calls,
$0.49, 1,828 entries, 8,921 renditions added, 0 failures. Completed.

**Stage 14 started 04:13 — graph-hygiene** (free; Pearce–Kelly cycle break after the
sense merges), then example-hygiene, vocabulary-hygiene.

**Stage 14 result — graph-hygiene (04:13, 35s):** 3,023 entries changed, $0. Completed.

**Stage 15 started 04:13 — example-hygiene** (cap $3).

**Stage 15 result — example-hygiene (04:13 → 04:18, 4m41s):** 2,688 calls, $0.25, 2,492
entries changed. Completed.

**Stage 16 started 04:18 — vocabulary-hygiene** (Dale–Chall rewrite of grade-1/grade-5
renditions that use hard words; cap $6).

**Stage 16 result — vocabulary-hygiene (04:18 → 04:29, 11m28s):** 3,907 calls, $0.46,
2,603 entries changed. Completed. (Note: it could not read `crave.json` — see
QA-DIARY iteration 5 — so that entry is picked up by the follow-up's passes.)

**Stage 17 started 04:29 — per-sense example generation** (8 verified sentences per
sense, D-53; cap $45). Tier-2 spend ≈ $109.

**Accounting gap found 05:15 (2026-09-03):** the pooled sweeps that own their own worker
pool — `examples`, `content_hygiene`, `relation_hygiene`, `sense_hygiene` (and the new
`contrasts`) — emit no per-item ledger records; their `runs/<id>.ledger.jsonl` stays
empty and only the final printed summary carries the cost. Per-call cost is still
recorded in each entry's provenance, which is how the running examples stage was measured
(**$4.24 for 4,566 entries, $0.0009/entry, at 05:15**). Fix after the chain: have the
pooled sweeps write ledger records like the CLI-driven loops do. Not changed now because
the running process imports the code.

## Retrieval-data feature build (2026-09-03, 05:00–06:00)

Nine features from `docs/RETRIEVAL-DATA-PLAN.md`, built by nine subagents in separate
worktrees and merged on branch `retrieval/integration` (19 commits over `main`, 36 files,
+14,293 lines; ruff / format / ty / pytest all clean, 900+ tests). Not merged to `main`
until the tier-2 chain and follow-up finish, since the chain imports the main checkout.

| Feature | Decision | Measured on `data/sample-300` (300 core entries) |
|---|---|---|
| Schema: `Query`, `QAPair`, `Contrast`; new stage names and flags | D-62 | 300/300 pre-addition entries validate unchanged |
| F1 `export-pairs` (WiC + positive pairs) | D-54 | 22,684 pairs; free |
| F3+F4 `export-triples` / `export-qrels` | D-56 | 7,043 triples with generated queries (2,038 with gloss pseudo-queries); qrels grades 3/2/1/0 = 1,041/48/57/3,123; free |
| F7 register diversity target + near-copy check | D-59 | existing near-copy rate 0.34%; diversity mean 0.72 |
| F8 `qc filler` | D-60 | 92% of encyclopedia renditions carry an over-threshold phrase vs 1.2% of examples — thresholds need tuning before `--flag` |
| F9 `export-pretrain` (4 templates) | D-61 | 2,400 docs / 720,565 words at grade_5+college |
| F2 `queries` (doc2query, 8 styles) | D-55 | luna $0.000245/sense, 78% headword-free, beat nano on cost and quality |
| F5 `contrasts` ("X vs Y" + verdict) | D-57 | $0.000124/contrast, 48 paragraphs, verdicts 29 typed / 19 differently / 0 unrelated |
| F6 `qa-pairs` (7 types, grounded) | D-58 | $0.000413/sense, $0.000060/pair, 98.4% grounded |

Findings from the pilots worth acting on: contrasts' `related_differently` verdicts mostly
flag relations resolved to the wrong POS (free work list for relation-hygiene); qa-pairs
has 7.9% meta-reference leakage ("the example…") and 11.6% of definition answers echo the
gloss; both are regex post-checks to add before a full run. The shared sample store was
written to by several pilots at once (cross-process writes are last-writer-wins), so the
sample's pilot output is partial; per-agent sample copies next time.

**Stage 17 result — per-sense example generation (04:29 → 08:44, 4h15m):** 54,431 calls
(luna generation + nano sense-check), **$25.67**, 31,883 entries changed, 0 downgrades,
completed under the $45 cap. Cache hit rate 0.60 (lower than the rendition stages: the
per-sense prompt carries more volatile text). The stage's own ledger is empty (see the
accounting note above); the cost is from its printed summary and matches the
provenance-derived running total. Tier-2 spend ≈ $135.

**Stage 18 started 08:44 — encyclopedia renditions, grade_5 + college** (cap $45).

**Stage 18 mid-run note (10:35):** the encyclopedia run hit three consecutive flex
capacity rejections at 09:18 and downgraded itself to the `auto` tier (D-40's
mechanism, first time it has fired in production). Effect: throughput roughly doubled
(82 → ~180 entries/min) and cost per entry roughly doubled ($0.00078 → $0.00150). At
19,868 / 31,886 entries and $28.48, the projected total is ≈ $46.5 against the $45 cap,
so the stage will most likely stop on budget a few hundred entries short. Added an
"encyclopedia remainder" stage (cap $12, back on flex) to the front of the follow-up
script, which was still waiting for `DONE-TIER2`; relaunched it (one waiter, verified).

## Writer-diversity pilot (2026-09-03, 10:00–11:20) — branch `retrieval/writers`, D-63

Frozen spec on 300 tier-2 entries, only the writer varied; two tasks (graded example
renditions, D-53 per-sense examples); five writers. Full report:
`docs/WRITER-DIVERSITY.md`. Ledger-sourced results:

| writer | worked? | cost / rendition | judge (Opus, n≈35) | distinct-4-gram | any-flag rate |
|---|---|---|---|---|---|
| gpt-5.6-luna (existing) | baseline | — | 64.2 | 0.890 | 1.9% |
| claude-haiku-4-5 | both tasks | $0.00373 | 62.8 | 0.991 | 0.7% |
| gemini-3.7-flash | renditions only; D-53 schema rejected (400) | $0.00433 (4.7× haiku's tokens) | 64.6 | 0.979 | 1.6% |
| qwen3.5-397b (OpenRouter) | yes, but reasoning blow-ups to 8K tokens, ~30× cost on bad calls; leaks prompt labels ("grade_10") into text | unpredictable | 64.5 | 0.984 | 1.2% |
| deepseek-v4-pro (OpenRouter) | no: pydantic-ai marks it as not supporting native structured output | $0 | — | — | — |

Attribution (TF-IDF + LR, 4 writers, balanced 517 each): 66% vs 25% chance; luna is
most confused with haiku; qwen most separable (label leakage). Confounded by uneven
per-arm coverage (each arm's budget stopped at a different point in the alphabetical
list) — re-measure on a matched subset before using it as a target metric. Headword
anchoring ≥ 98% for every writer. Judge scores within 1.8 points across arms.

**Recommendation adopted for evaluation:** rotate luna (majority) with haiku (minority,
~20%) on RENDITIONS and EXAMPLES via the new `ModelPolicy.writers` / `writer_for`
(deterministic by sense id; provenance records `provider`). Gemini is a candidate for
renditions once its D-53 schema failure is understood; qwen and deepseek are out.
Not yet enabled on the store.

Plumbing shipped: multi-provider router (Anthropic, Google, OpenRouter, local base-URL),
price rows for the tested writers, `Provenance.provider`. Two housekeeping findings:
`stages._RETRYABLE_STATUS` lacks 529 (Anthropic "overloaded"), which cost 5–7 judged
entries per arm; and the pilot's research subagent exceeded its read-only brief and ran
paid arms itself — the pilot's ~$30 total (judge ≈ $11, writers ≈ $4, plus the
duplicate runs) includes that. No production store was touched.

**Stage 18 result — encyclopedia grade_5 + college (08:44 → 11:39, 2h54m): stopped on
its $45 cap.** 34,246 calls, **$45.00**, 30,408 / 31,886 entries, 60,816 renditions
added, 0 failures, `flex_downgraded=True` (from 09:18), cache hit rate 0.66. The
remaining 1,478 entries run first in the follow-up (cap $12, flex). Tier-2 spend ≈ $180.

**Stage 19 started 11:39 — retrofit readability_hygiene + rendition_hygiene**, then
audit → `DONE-TIER2`.

**Stage 19 did not run.** `opengloss retrofit --only` takes a single pass name; the
chain passed `readability_hygiene,rendition_hygiene` and the CLI rejected it (stderr was
discarded by the chain, so it surfaced only as the JSON parser's traceback). Queued a
second follow-up (`$SP/tier2_followup2.sh`) that runs the two passes separately and
re-audits after the first follow-up finishes.

**Stage 20 — audit at 11:39, and `DONE-TIER2` at 11:39:51.** Tier-2 coverage
(31,886 entries, 76,855 live senses): gloss 9 renditions ≥ 99.95%; example levels
160–195% (multiple per level after D-53); example registers (formal / informal /
slang / technical, written by the D-53 per-sense stage) 83–91%; encyclopedia
grade_5 + college 95.4% (remainder running); explanation 100%; spans 99.97%; domain
100%; relations resolved 99.0% of in-core targets; 0 hypernym cycles; synonym
reciprocity 98.4%, antonym 99.7%. Open: 19,938 readability-miss flags await the
retrofit passes above; relation-validity and sense-hygiene remainders are in the
follow-up. Chain spend ≈ $180.

**Follow-up 1 — encyclopedia remainder (11:40 → 11:52, 12m):** 1,712 calls, $2.10,
1,478 entries, 0 failures, completed (flex downgraded again after 3 rejections, so the
capacity squeeze on luna flex is persistent today). Encyclopedia grade_5 + college now
at 100% of tier 2. Tier-2 spend ≈ $182.

**Follow-up 1 — sense-hygiene remainder started 11:52** (cap $8).

**Follow-up 1 — sense-hygiene remainder (11:52 → 12:07, 15m):** 7,143 calls, $1.94,
completed. `distinctness` made 0 calls over 41,886 entries scanned — the "unjudged"
entries from the marker census were single-sense entries with nothing to compare, so
the census overstated the remainder. `example_fit`: 1,175 examples moved, 1,855 removed,
628 senses emptied (repair regenerates them next), 1,566 entries changed.

**Follow-up 1 — repair started 12:07.**

**Follow-up 1 — repair (12:07, 53s):** 428 calls, $0.04, completed.

**Follow-up 1 — relation-hygiene validity remainder started 12:08** (cap $20).

## Writer-diversity pilot, round 2 (2026-09-03, 13:00–13:40) — branch `retrieval/writers2`, D-64

| arm | result |
|---|---|
| gemini-3.8-flash (direct) | judge **64.9** (best of all writers), any-flag rate **0.09%** (best), $4.68 incl. judge; but mean 3,714 output tokens/call on per-sense examples (up to 6,830) → 10% hard failures from JSON truncation at max_tokens 8192; coverage only 41/300 entries within the $0.75 cap |
| z-ai/glm-5.2:free | 0 calls: pydantic-ai's OpenRouter profile registry says "native structured output not supported" (client-side) |
| nvidia/nemotron-3-super-120b:free | same client-side refusal |

**Gemini schema diagnosis:** the 400 on `DraftExampleBatch` is Gemini's structured-output
compiler rejecting a `list[...]` whose *encoded schema weight* (item schema size ×
`maxItems`) exceeds an internal budget; under `NativeOutput(strict=True)` the real
contract passes at `maxItems=32` and fails at 40. The agent lowered
`MAX_EXAMPLE_SENTENCES` 200→32 to get the arm running; that makes 7.3% of entries (>4
live senses) fail for every writer, so the cap is **kept at 200** on integration and the
Gemini path needs a provider-aware batch split (open in D-64).

**Matched-subset attribution:** 52.7% unmatched → **38.6%** on the 27-headword subset
every writer covered (5 writers, chance 20%). Round 1's 66% was inflated by uneven
coverage, as suspected.

**Free models, follow-up probe (this session):** the refusal is pydantic-ai's model
profile, not the server. With `OpenAIModelProfile(supports_json_schema_output=True)`
passed to `OpenRouterModel`, **nemotron-3-super-120b:free answered strict JSON in 6–7 s,
~500 output tokens for three sentences** (reasoning included), sentences usable;
glm-5.2:free returned upstream 429 "temporarily rate-limited" on every try (free-tier
congestion). So a free arm is feasible for nemotron via a one-line profile override in
the router, at 20 req/min; not yet piloted end to end. Note the nemotron sample
included a "the researcher demonstrated…" sentence — the stilted-register pattern the
content-hygiene pass exists to catch.

Recommendation stands: luna/haiku 80/20 on renditions and examples. Gemini-3.8 is the
highest-quality writer measured but needs the batch split and a verbosity cap before it
can carry a share.

**Follow-up 1 — relation-hygiene validity remainder (12:08 → 13:52, 1h44m): stopped on
its $20 cap.** 27,282 validity calls, **$20.02**, 32,030 entries changed: 200,358 edges
demoted, 44,622 far-side demotions, 28,722 retyped. (The free `inflections` step also
demoted 1,308 more.) Marker census afterwards: 34,921 judged, **6,965 still without a
marker** (some of those have no edges to judge). Added a second validity pass (cap $8)
plus graph-hygiene to the front of follow-up 2. The validity step has now demoted or
retyped ~430K edges across the whole store, by far the largest single correction in the
tier-2 pass — consistent with the Opus judge's 88% relations-invalid rate on tier 2
before it ran. Spend ≈ $206.

**Follow-up 1 — graph-hygiene started 13:52**, then example catch-up and audit.

**Follow-up 1 — graph-hygiene (13:52, 46s):** 4,409 entries changed, $0. Completed.
**Follow-up 1 — example renditions catch-up started 13:53.**

**Follow-up 1 — example renditions catch-up (13:53 → 13:55, 2m):** 871 calls, $0.14,
587 entries, 0 failures. Completed. Audit running; then `DONE-FOLLOWUP` hands over to
follow-up 2 (validity pass 2, graph-hygiene, readability_hygiene, rendition_hygiene,
final audit).

**Follow-up 1 — final audit and `DONE-FOLLOWUP` at 13:55.** Tier 2: encyclopedia
grade_5 + college **100%**; gloss 9 renditions ≥ 99.95%; example levels 160–194%,
example registers 83–90%; explanation 100%; 0 hypernym cycles. Relations: 948,440
total, 99.0% of in-core targets resolved. Reciprocity moved from 98.4% → **94.9%**
(synonym) and 99.7% → **97.6%** (antonym) after the validity pass demoted ~200K edges on
one side while 6,965 far-side entries were still unjudged; validity pass 2 and
graph-hygiene in follow-up 2 should close most of that gap — check the final audit.
Readability-miss flags 19,970, awaiting the retrofit passes in follow-up 2.

**Follow-up 2 — validity pass 2 (13:56 → 14:27, 32m): stopped on its $8 cap** after
11,475 calls and 15,500 entries changed, but the "never judged" count only moved
6,965 → 6,282: the digest-keyed markers mean every far-side demotion invalidates the
neighbour's marker, so a whole-store sweep mostly re-judges moved entries (bounded at 2
attempts) before it reaches the never-judged tail. Census: 6,173 entries with resolved
edges and no marker, 104 with only unresolved edges, 5 with none. Cumulative validity
spend $40. Added `--from-list` to `relation-hygiene` (integration branch, a00b28b) and
queued follow-up 3 to judge exactly those 6,173 (cap $10) from the integration worktree,
then graph-hygiene and a final audit. Graph-hygiene after pass 2: 2,311 entries, 35s.

**Follow-up 2 — retrofit readability_hygiene started 14:28** (19,970 flagged renditions).

**Power outage ~14:56 (2026-09-03); machine back 15:42.** The store's last write was
14:56:29, during follow-up 2's `readability_hygiene` retrofit (started 14:28). After
reboot: 41,886 entries, all but one validate (`calendaring.json`, being inspected), one
stray `.tmp` from an interrupted atomic write, 48 stale per-entry lock files (owners
dead; deleted). The scratchpad under `/tmp` was wiped: the follow-up scripts, their
logs, the audit JSONs, the exports and the unjudged-headword list are gone; the diary,
the worktrees and `runs/` are on disk and intact. Everything that was queued is
idempotent: `readability_hygiene` and `rendition_hygiene` retrofits, the targeted
validity pass (list regenerated from the markers), graph-hygiene, final audit — being
re-queued as one script.

**Post-outage repair (15:45–16:00):** `calendaring.json` had a duplicate `grade_1/plain`
example rendition written by the `readability_hygiene` retrofit — the same collision
class as `crave.json` (QA-DIARY iteration 5), in a second code path. Fixed on the
integration branch (`_example_collides` in `retrofit.py`, regression test) and the
duplicate dropped from the file. The stray `.illnesses.json.*.tmp` was an interrupted
atomic write; the real file validates; tmp removed. Remaining passes re-queued as one
resume script run from the integration worktree (so both collision fixes and
`relation-hygiene --from-list` are in effect): readability_hygiene ($8) →
rendition_hygiene ($4) → targeted validity on 6,173 entries ($10) → graph-hygiene →
final audit.

**Resume — retrofit readability_hygiene (15:48 → 16:09, 21m):** 7,879 calls, **$2.09**,
6,906 entries, 10,170 renditions rewritten: 8,556 now in band, 5,951 still out of band
after their bounded attempts. Completed. (Together with the ~28 minutes that ran before
the outage, whose provenance is on the entries, the pass cost ≈ $3.)

**Resume — retrofit rendition_hygiene started 16:09.**

**Resume — retrofit rendition_hygiene (16:09 → 16:10, 52s):** 302 calls, $0.02, 1,658
entries: 309 headword-initial renditions rewritten (2,784 still initial after their
bounded attempts, mostly proper nouns), and the new F7 near-copy check flagged 1,504
register renditions `og.near_copy` (flag only, no spend). Completed.

**Resume — targeted validity pass started 16:10** (6,173 entries, cap $10).

**Resume — targeted validity pass (16:10 → 16:31, 21m): completed within cap.**
6,523 validity calls over the 6,173 named entries, **$4.79**, 11,905 entries changed
(the far-side demotions touch neighbours). Every entry with resolved edges now carries a
validity marker. Cumulative validity spend ≈ $45 across the four passes.

**Resume — graph-hygiene started 16:31**, then the final audit.

**Resume — graph-hygiene (16:31, 34s): 1,208 entries. Final audit 16:32; `DONE-RESUME`
16:32:54. Tier 2 is complete.**

Final audit, 31,886 entries / 76,855 live senses, versus the mid-chain audit:

| metric | mid-chain (04:30) | final (16:32) |
|---|---|---|
| readability-miss flags | 19,591 | **5,296** |
| headword-initial gloss renditions | 395 (0.08%) | **83 (0.02%)** |
| encyclopedia grade_5 + college | 0% | **100%** |
| synonym edges asserted | 98,455 | 68,844 (30% demoted by validity) |
| synonym reciprocity | 98.4% | 93.7% |
| antonym reciprocity | 99.7% | 96.9% |
| hypernym cycles | 0 | 0 |
| validity-judged entries (whole store) | 17,485 | **41,759** (18 left with edges) |

Reciprocity fell because the validity judge demoted ~30% of synonym edges and its two
directional verdicts do not always agree; ~4,400 synonym edges now lack a reciprocal.
Open item: a reconcile pass that applies the stricter verdict to both sides, or
re-judges the asymmetric pairs. Cumulative tier-2 spend ≈ $230 (chain $180, follow-ups
and resume ≈ $50), against the $274 sum of caps.

**Schema note:** entries written by the integration-worktree passes carry the D-62
keys (`queries: []`, `qa: []`, `contrasts: []`, `provider: null`), which the pre-merge
schema rejects (`extra="forbid"`). `main` was fast-forwarded to the integration branch
at 16:20 (3e44dee), so every consumer must run on that code from now on; ~45% of files
already carry the new keys.

## Goal 2 — finish the tier-2 store (2026-09-03, from 16:50)

Plan: (1) relation-reconcile pass (asymmetric verdicts → stricter side; tombstone
demoted `see_also` edges out of the list the judge reads; per-type cap), (2) D-46 domain
retag if a luna pilot beats nano on `domain_fits`, (3) filler rewrite on flagged
examples; each verified by a forced re-judge of the same 40 entries (seed 7; baseline
68.6). Then (4) the luna/haiku 80/20 rotation, (5) queries, contrasts and qa-pairs on
the whole store, caps at 1.5× the pilot-derived cost.

**16:50** — three agents launched in worktrees off `main`: `relation-reconcile`
(Opus), `filler_examples` content-hygiene step + threshold calibration (Sonnet), domain
retag pilot luna vs nano on the 40-entry judge sample (Sonnet). Measured before
launching: relations/sense mean 13.4 (see_also 49%, mostly demotions); the judge is
shown every relation including demoted see_also, which is why relation defects stayed
at 84% after ~430K demotions. Domain leaves 159, `.general` 11.7%. `og.filler` flags
on examples: 0 (never run with `--flag`).

**17:05** — rotation enabled on `main` (5252ea1): `writers=[luna 0.8, haiku 0.2]` on
RENDITIONS, EXAMPLES, QUERIES, CONTRASTS, QA_PAIRS; writer keys added at the three
pair-stage call sites. 300-entry pilot of the three pair stages started on the store
(top-300 tier-2 headwords; caps $3/$3/$4) to derive whole-store caps.

**17:49 — queries pilot (300 entries, 890 senses) done:** 890 calls, **$0.875**, 10,679
queries stored (12/sense), 79% headword-free, 882/890 senses with all 8 styles, 7
rejected (surplus). Rotation drew haiku for 19% of senses. **Cost is 4.0× luna-only**
($0.00098/sense vs $0.000245): haiku gets no prompt-cache hits (its 4,096-token cache
minimum exceeds our ~2K instruction blocks) and lists at 5× luna's flex price. See
`docs/WRITER-DIVERSITY.md` "Cost correction" — the earlier "few percent" claim was
wrong. Contrasts pilot running (haiku $0.0068/call vs luna $0.00054 so far).

**17:59 — pair-stage pilots complete (300 entries, rotation live):**

| stage | calls | cost | per sense | haiku share of cost | output |
|---|---|---|---|---|---|
| queries | 890 | $0.88 | $0.00098 | 80% | 10,679 queries, 79% headword-free |
| contrasts | 271 | $0.47 | — ($0.00032/contrast) | 75% | 1,458 contrasts; verdicts 1,013 typed / 428 differently / 17 unrelated |
| qa-pairs | 890 | $1.46 | $0.00164 | 78% | 6,090 pairs (7 types evenly), 140 dropped (116 not grounded) |

Whole-store extrapolation (110,869 live senses; contrasts by entry ratio): queries
≈ $109, contrasts ≈ $66, qa-pairs ≈ $181 → **≈ $356 with the 80/20 rotation** vs
≈ $95 luna-only. Whole-store run held pending the user's choice (options in
WRITER-DIVERSITY.md "Cost correction"); default if unanswered when the quality passes
finish: luna-only on these three stages, rotation kept on renditions/examples.

**18:05 — domain-retag pilot (D-67, merged):** on the 40-entry judge sample, luna
retagged 45/98 senses and cut the `domain_fits` defect rate **22.4% → 14.3%** at
$0.000039/sense (27% cheaper than nano). D-46's version bump only clears `.general`
tags (15% of senses), so a new `--force-retag-domains` flag clears every live sense.
Whole-store retag launched: luna, cap $6.5 (1.5× the $4.34 extrapolation), followed by
the forced 40-entry re-judge.

**18:10 — relation-reconcile pass (D-65, merged):** three free steps — `asymmetric`
(apply the stricter directional verdict), `tombstone` (move demoted `see_also` edges
out of the relation list into a provenance note, so the judge stops reading them),
`cap` (per-type caps: synonym 8, antonym 4, hypernym 3, hyponym 8, others 4, with a
far-side phase so a cap never creates asymmetry). On its 300-entry sample: synonym
reciprocity 93.5% → **100%**, antonym 97.9% → **100%**, mean relations per sense
22.4 → **7.1**, and graph-hygiene afterwards changed nothing. Runs on the store after
the retag re-judge, then its own re-judge.

**Latent bug fixed on main (bbf96d3):** every "latest marker" lookup iterated
`provenance.values()` in table order, but the store writes sorted JSON keys, so past 99
records `p100` precedes `p2` and the wrong record was taken as latest. Effect: entries
with 100+ provenance records were re-judged on every sweep (part of why validity pass
2 spent $8 re-judging). New `Lexeme.provenance_in_order()`; eight lookups switched;
regression test.

**18:20 — filler rewrite (D-66, merged):** `qc filler --fields examples` calibrated
read-only on the full store (1,228,673 example renditions): the plan's thresholds flag
3.7%, the chosen defaults (4-gram 0.025%, opener 0.25%, min count 5) flag **6.5%**,
with a legible top-25 ("the museum displayed a", "as a hereditary surname"; opener
"after the" on 0.9% of all example sentences). New content-hygiene step
`filler_examples` rewrites flagged examples naming the phrase to avoid; refuses
rewrites that drop the headword or collide with a sibling. Pilot: 726 flagged of
9,146, 708 rewritten, 18 refused, **$0.0000534/rewrite**; idempotent on rerun. Queued
on the store after the retag re-judge: reconcile → graph-hygiene → re-judge → filler
flag + rewrite (cap $6.5) → re-judge → audit.

**Domain retag, whole store (18:06 → 19:37):** hygiene clear 110,870 domains ($0,
4m); luna `tag_domain` 41,881 calls, **$3.90** (extrapolation was $4.34; cap $6.5),
110,869 senses retagged, cache hit rate 0.94, completed. Forced re-judge running.

**Relation-reconcile, whole store (19:42 → 19:51, 9m, $0):** `asymmetric` demoted
18,448 one-sided edges (synonym 14,071, antonym 4,366, confusable 11); `tombstone`
moved **751,249** demoted `see_also` edges out of relation lists into provenance notes;
`cap` removed 145,716 over-cap edges across 12,862 senses (antonym 97,064, synonym
35,959, hypernym 7,953, hyponym 2,816, instance_of 1,556) plus 64,742 far-side
removals to keep pairs symmetric. Relations per sense on a 3,000-entry sample:
mean **13.4 → 5.5**, median 10 → 5, p90 22 → 10; what remains is 32% synonym, 24%
hypernym, 20% hyponym, 12% antonym, no demoted see_also. Graph-hygiene running, then
the re-judge.

**19:55 — pair stages queued (whole store, after the quality passes).** No answer yet
on the rotation-cost question, so the stated default applies: **luna-only for queries,
contrasts and qa-pairs** (`scripts/pair-stages-luna-only.toml`; rotation stays on
renditions and examples). Caps = 1.5× the luna-only pilot extrapolations: queries $41
(est. $27), contrasts $38 (est. $25), qa-pairs $69 (est. $46). Input: all 41,885
headwords (core + tier 2). Then a final audit and the four free exports
(`export-pairs`, `export-triples`, `export-qrels`, `export-pretrain`) into
`data/exports/` for the encoder.

**Filler flag, whole store (19:54, $0):** 1,228,673 example renditions scanned;
**80,055 flagged** (6.5%, matching the calibration) across 30,772 entries; 15 over-
threshold 4-grams ("the museum displayed a" ×1,221, "reappraise…" ×465 …), no
over-threshold openers at the chosen threshold. Rewrite pass started 19:57 (cap $6.5).

**Filler rewrite, whole store (19:57 → 21:02, 65m):** 30,772 calls (one per flagged
entry), **$4.77**, 79,533 of 80,055 flagged example renditions rewritten, 522 refused
(headword dropped or sibling collision), 0 failures, completed under the $6.5 cap.
$0.00006 per rewrite, matching the pilot. Re-judge running, then the final audit and
`DONE-QUALITY` hands over to the pair stages.

**21:06 — quality passes complete (`DONE-QUALITY`).** Tier-2 audit after retag +
reconcile + filler: synonym reciprocity **99.5%** (was 93.7%), antonym **99.97%**
(was 96.9%), 0 hypernym cycles; relations 948K → **416K** (the tombstoned demotions
now live in provenance notes); headword-initial glosses 83 (0.02%). New:
`senses_zero_relations` 3 → **1,760** — senses whose every edge was a demotion; they
are candidates for a targeted relation regeneration (open item). Spend for the three
passes + four judges: retag $3.90, reconcile $0, filler $4.77, judges $13.4 → **≈ $22**.
Pair stages started (luna-only default).

**21:50 — `verdicts` step merged (D-68):** contrast verdicts now feed the graph.
`unrelated` and `related_differently` edges are demoted to `see_also` (far side too for
symmetric edges) and tombstoned in the same sweep; converges in two sweeps. On the
contrasts pilot's 271 entries: 272 near-side + 222 far-side demotions, all 9
`unrelated` verdicts right on inspection; of `related_differently`, only 28% were POS
mismatches — the dominant pattern (11 of 15 read) is a synonym that is really a
hypernym/hyponym (falcon→peregrine, dictator→pharaoh). Demoting to `see_also` loses
that information; a retype-by-reading-the-paragraph step is the named follow-up. The
post-pair waiter now runs the full reconcile twice after the contrasts stage covers
the whole store.

**22:05 — qa-pairs meta-reference post-check merged (D-69)**, ahead of the whole-store
qa-pairs run: 8 patterns ("according to the sources", "the passage", …), repair-then-
drop; on the pilot's 6,090 pairs: 116 matches (1.9%), 14 repairable, 4 false positives
(all "the passage" on senses that *are* passages). `echoes_gloss` (first-60-char rule)
matched 0 on this sample — the pilot's 11.6% were paraphrased restatements, not verbatim.

**22:20 — `circular_gloss` step merged (D-70):** the full detector (headword or any
inflected/derived form in the canonical gloss; proper nouns exempt) finds **21.9%** of
senses circular (13.4% by literal headword match, which is what the overnight scan
measured). Pilot: 179/181 rewritten over three sweeps, $0.0001/rewrite, two permanent
refusals stopped by the D-47 bound; whole-store extrapolation $1.3–2.2. Graded and
register renditions are left as they are (independently valid).

Queued behind the post-pair reconcile (`final_hygiene.sh`): circular_gloss (cap $3.5,
+ a $1 second sweep), stilted_examples rerun (cap $3), retrofit spans (cap $1), forced
re-judge, final audit. Everything for this goal is now merged on `main` and queued; the
remaining wall-clock is the pair stages (queries ~05:30, contrasts ~09:00, qa-pairs
~19:00 on 2026-09-04), then ≈ 1.5 h of free/cheap passes.

**Queries, whole store (2026-09-03 21:07 → 2026-09-04 03:42, 6h35m):** 109,980 calls
(one per live sense), **$26.67** (extrapolation $27; cap $41), 41,581 entries changed,
**1,319,632 queries stored** (12/sense), 76.8% headword-free, 109,168/110,870 senses
with all 8 styles, 1,264 rejected (surplus), 0 failures, 0 429s, cache hit rate 0.89.
By style: question 227K, keyword 218K, conversational 179K, example_based 171K,
directive 168K, constraint 131K, role/step_by_step ~112K each. Contrasts started 03:42.

**Contrasts, whole store (03:42 → 05:15, 1h33m):** 24,674 calls, **$8.95**
(extrapolation $25 — the reconcile had already removed most edges, so far fewer pairs;
cap $38), **83,120 contrasts stored**, 357 rejected. Verdicts: related_as_typed
58,693 (70.6%), **related_differently 24,183 (29.1%)**, unrelated 244. 12,112 edges
over the per-call cap (a second sweep would cover them), 76,362 deferred to the far
side, 116,219 skipped as unresolved. The post-pair reconcile will demote the 24,427
`related_differently`/`unrelated` edges (D-68). QA-pairs started 05:15.

**2026-09-04 06:40 — pushed to GitHub.** Until now every commit (67 on `main`) was
local only; `main` and the 19 merged feature branches are now on
`github.com/mjbommar/opengloss-generator` (public). Secret scan of the tree: clean.
Rule from here: push after every merge to `main`.

**QA-pairs, whole store (05:15 → 11:09, 5h55m):** 109,980 calls, **$47.92**
(extrapolation $46; cap $69), **744,258 pairs accepted** (6.8/sense; easy 307K, medium
313K, hard 124K; all seven types 100–109K each), 25,630 dropped: meta_reference 12,085
(D-69's post-check, 1.6% of generated), not_grounded 9,647, unknown_citation 3,090,
echoes_gloss 808. $0.000064 per accepted pair. 0 failures, 0 429s. Audit + exports next.

**Pair stages total: $83.54** (queries $26.67, contrasts $8.95, qa-pairs $47.92) against
$148 of caps and a $95 luna-only estimate. Goal-2 spend to date ≈ $106 (quality passes
$22 + pairs $84); ≈ $10 of cheap passes remain.

**Exports (11:10 → 11:23, free) → `data/exports/`:** `pairs.jsonl` 7,350,478 WiC-style
pairs (3.0 GB); `triples.jsonl` 2,641,578 query/positive/hard-negative triples from
1,330,311 queries (3.4 GB); `pretrain.jsonl` **108.3M words** of natural documents at
grade_5 + college (dictionary / thesaurus / encyclopedia / usage-note templates; 0.8
GB). `export-qrels` failed in the script on a flag name (`--out` vs `--out-dir`) and is
being rerun by hand. `DONE-PAIRS` 11:23; the post-pair reconcile started.

**Post-pair reconcile, sweep 1 (11:24 → 11:29, 5m, $0):** `verdicts` demoted
**44,660** edges (near side 24,962 = the 24,183 `related_differently` + 439 `unrelated`
+ …; far side 19,698), all tombstoned in the same sweep; `dedup` removed 6,723 exact
duplicate edges; `cap` trimmed 23; `asymmetric` found nothing left to reconcile.
21,800 entries changed. Sweep 2 (tombstoning the far-side demotions) running.

**Post-pair reconcile sweep 2 + graph-hygiene + audit (11:29 → 11:34, $0):** sweep 2
tombstoned the far-side demotions (11,649 entries) and reconciled 151 more one-sided
edges; graph-hygiene changed 755. Audit: synonym edges 54,952 → **41,606** (the
verdicts removed a quarter), reciprocity synonym 99.5% → 97.6%, antonym 99.97% →
98.7% (a few hundred pairs the two sweeps did not fully close; a third sweep is free
and can run any time), 0 cycles; `senses_zero_relations` 1,760 → **2,142** — the
verdicts emptied ~380 more senses whose only edges were mistyped. That regeneration
item grows accordingly. `DONE-POST-PAIRS` 11:34; final hygiene started.

**Qrels export (11:32 → 11:45, free):** `data/exports/qrels/` — 8,671,844 graded
judgments for 1,330,311 queries (grade 3: 1.33M own-sense docs; 2: 947K synonym
glosses; 1: 2.40M hypernym/co-hyponym; 0: 3.99M unrelated), plus `docs.jsonl` and the
listwise JSONL. All four exports now on disk (≈ 8 GB).

**Circular-gloss rewrite, sweep 1 (11:35 → 12:04, 29m):** 13,991 calls, **$1.93**
(extrapolation $1.3–2.2; cap $3.5), **21,379 canonical glosses rewritten** across
13,484 entries, 992 refused (still circular / drifted / collision), completed. Sweep 2
(cap $1) running for the refusals' second attempt, then stilted rerun and spans.

**Final hygiene complete (`DONE-FINAL-HYGIENE` 12:11):** circular_gloss $1.93 + $0.05,
stilted rerun $0.02, spans $0.01, judge $3.40. Audit: `gloss_starts_with_headword`
39 → **0**; relations 386K; 0 cycles. Closing reconcile sweep + graph-hygiene + audit
running.

## Goal 2 complete — 2026-09-04 12:14 (`DONE-CLOSING`)

Closing sweep changed 111 entries; graph-hygiene 0. Closing audit: relations 385,949
(from 948K at the start of the day); synonym reciprocity 97.6%, antonym 98.7% — the
residual ~1,000 one-sided pairs are ones whose far side is unresolved or retired, which
the reconcile cannot match (open item); 0 cycles; `gloss_starts_with_headword` 0;
`senses_zero_relations` 2,143 (open item: targeted relation regeneration).

**Ledger for goal 2** (all from run summaries):

| stage | cost |
|---|---|
| domain retag (luna, whole store) | $3.90 |
| relation reconcile ×4 sweeps, dedup, verdicts | $0 |
| filler rewrite (79,533 examples) | $4.77 |
| queries (1.32M) | $26.67 |
| contrasts (83,120) | $8.95 |
| qa-pairs (744,258) | $47.92 |
| circular-gloss rewrite (21,613 glosses) | $1.98 |
| stilted rerun + spans | $0.03 |
| six Opus re-judges | $20.20 |
| **total** | **$114.42** |

No stage exceeded its 1.5× pilot-derived cap; the three pair stages came in at 56% of
their caps. Judge on the fixed 40-entry sample: **68.6 → 70.2**; relations defect
84% → 60%; domain 29% → 21%; gloss accuracy 14% → 12%. Writer rotation: enabled on
RENDITIONS and EXAMPLES (D-63); the three pair stages ran luna-only after the
measured 4× cost of haiku's share was reported and no reply arrived before launch.
Exports for the encoder in `data/exports/` (≈ 8 GB). Everything on `main`, pushed.

Open items carried forward: (1) regenerate relations for the 2,143 senses left with
none; (2) a retype-by-reading-the-contrast step so `related_differently` edges become
hypernym/hyponym instead of `see_also`; (3) the ~1,000 unmatched one-sided pairs; (4)
`examples_fit_sense` / `distinct_from_other_senses` have not moved — strengthen the
nano sense-check; (5) Haiku prompt caching needs a ≥ 4,096-token prefix to engage.

**D-71 fix merged and exports regenerated (13:14 → 13:31, free).** Encyclopedia text
is a positive only for monosemous entries. Re-exported: `triples.jsonl` 2,639,059
(encyclopedia positives 2.9%, **0.00% on polysemous entries**, was 30.5%);
`pairs.jsonl` 7,250,395 (−100K polysemous `example_encyclopedia` pairs);
`qrels/` 10,001,904 judgments (encyclopedia docs now graded 3 on monosemous entries,
1 on polysemous ones; docs.jsonl grows to include them); `pretrain.jsonl` unchanged.
Final integrity check: every store file validates; every export file well-formed.

**Goal 2 closed at 13:31 on 2026-09-04.** Total for the goal $114.42; the fix and
re-export cost nothing.

## Goal 3 — tier 3 (docs/TIER3-PLAN.md, recipe A) — 2026-09-04 14:05

Inflection fold: 21,061 of the 33,899 ranked-remainder words are inflected forms of
already-enriched lemmas (v1.3 stored them as separate lexemes with mirrored senses);
folded. **12,838 new lemmas** in `data/core/tier3_final.tsv`. Migrating them from the
v1.3 source (free), then the recipe-A chain: retrofit passes, resolve, gloss × 4
levels, examples × 4 levels, encyclopedia grade_5 + college, hygiene block, reconcile,
judge sample, audit. Caps sum $96; expected ≈ $66.

**14:21 — migration done** (12,838 files, 0 failures, 10 min; store now 54,724
entries). **Chain gotcha #6:** `retrofit` and `resolve` take no `--from-list`; the
first launch's structural stages exited in 2 s each on a usage error (stderr was in
the per-stage error files, not the log). Killed before the paid stages, fixed to
store-wide runs (`retrofit --only <pass>`, `resolve --all`; markers skip the
enriched 42K), relaunched 14:22.

**Tier 3 structural stages (14:22 → 15:32):** classify_kind $0.01 (94% deterministic),
hygiene $0.07 (12,874 entries), tag_domain (luna) **$1.10** (30,203 senses), spans
$0.10, repair $0.01, resolve **$4.12** (18,958 calls, completed under the $8 cap).
Structural total **$5.41** vs the $5 expected. Graph-hygiene, then the three luna
rendition stages.
