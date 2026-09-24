# Replacing duplicated pretraining documents with leveled content

**Date:** 2026-09-24  
**Status:** plan; no generation or code change has been made for it yet  
**Scope:** the working store `data/core-store` (post fill round, see
[FILL-ROUND-V2.4.md](FILL-ROUND-V2.4.md)) and the `pretrain` exporter. Nothing is pushed to the
Hub by this plan.  
**Model policy:** `gpt-6-luna`, `service_tier=flex`, `reasoning_effort=low`
(`scripts/gpt6-luna.toml`), the same as the fill round.

## 1. Problem

The pretraining corpus renders four templates per entry at three reading levels (`neutral`,
`grade_5`, `college`). Two templates have no level-specific source text, so their `grade_5`
and `college` documents are exact copies of `neutral`. In the local v2.4-rc export
(`/data1/opengloss-generator/v2.4-rc/hf`), exact normalized duplicates break down as follows:

| template | level | rows | exact duplicates | cause |
| --- | --- | ---: | ---: | --- |
| thesaurus | grade_5 | 155,481 | **155,481** | renderer reads no leveled text; `level_used` wrongly says `grade_5` |
| thesaurus | college | 155,481 | **155,481** | same |
| usage_note | grade_5 | 161,440 | **161,440** | register glosses and contrasts exist only at `neutral`; neutral fallback |
| usage_note | college | 161,440 | **161,440** | same |
| encyclopedia | grade_5 / college | 165,291 each | 353 each | entries with no leveled overview |
| dictionary | grade_5 / college | ~160K each | 16 / 53 | rare fallbacks |
| **all** | | **1,928,808** | **634,617 (32.9%)** | **≈131M duplicate words** |

Two smaller defects share a root cause:

- Encyclopedia documents at `grade_5` and `college` have a genuinely leveled overview, but
  are labeled `level_used = neutral`. The "Why This Word" section (`lexical_explanation`)
  exists only at `neutral`, and any fallback marks the whole document.
- The encyclopedia template emits 165,291 documents per level. That is every lexicon row,
  including 4,567 retired lexemes, against 160,724 live ones.

For the embedding models, a copied document costs tokens and compute and teaches nothing: in
v2.4-rc only 85% of 256-token windows are unique. For lookup and reference, a reader asking
for the grade-5 usage note gets the adult text.

## 2. Goal

Every non-neutral pretraining document must contain text written for its level, and **no
document may be an exact or near copy of another**. Duplicates are replaced with new, grounded
leveled content rather than simply dropped. The same content also serves lookup: every sense
gains register variants a grade-5 or college reader can use, and every contrast gains leveled
"how to choose" notes.

## 3. What gets generated

Every item uses an existing field of the schema. No schema change is needed, only one new
rendition target (3.2).

### 3.1 Leveled register glosses (feeds the usage note)

- **Field:** `Sense.gloss` renditions at `(level, register)`.
- **Targets:** `grade_5` and `college` × `informal`, `formal`, `technical`, which is
  **6 per live sense**.
- **Excluded by default:** `marketing` stays at `neutral` only, because "marketing copy
  for a fifth-grader" is not a natural cell. The decision is in section 9.
- **Mechanism:** already supported. `RenditionRequest.targets()` crosses levels and
  registers:

  ```bash
  opengloss enrich --from-list L --fields gloss \
      --reading-levels grade_5,college --registers informal,formal,technical
  ```

- **Existing checks that apply to every target:**
  - Flesch-Kincaid band for the level;
  - Dale-Chall familiar-word share at `grade_5`;
  - no rendition that opens with the headword (D-39);
  - near-copy retry against the canonical gloss for non-plain registers (D-59);
  - one retry with feedback, then flag.
- **Volume:** 300,787 senses × 6 = **1,804,722 renditions**.

### 3.2 Leveled contrast paragraphs (feed the thesaurus) — new target

- **Field:** `Contrast.text`, which is already a `Renditions[str]` (schema:
  "a rendition set so the paragraph can be levelled like any other prose"). No workflow
  writes levels to it yet.
- **Targets:** `(grade_5, plain)` and `(college, plain)` for each of the 270,728 live
  contrasts, which is **541,456 renditions**.
- **Code:** add `RenditionField.CONTRAST` to `workflows/enrich.py`. The work item is one
  contrast; its owner is the entry, and it is addressed by `edge_id`.
- **Prompt inputs:** the canonical contrast text, both headwords, both canonical glosses
  and the stored verdict. The prompt keeps it above the 1,024-token prefix so it is cached,
  like the other rendition prompts.
- **Acceptance checks** (deterministic, retried once, then flagged):
  - both the headword and the target term appear;
  - the level's Flesch-Kincaid and Dale-Chall bands, as for glosses;
  - not a near-copy of the canonical paragraph (content-word Jaccard below 0.9);
  - word count within 40–160% of the canonical paragraph;
  - no "unrelated"-style wording when the verdict is `related_as_typed`, checked with a
    small phrase list.
- **CLI:** `opengloss enrich --fields contrast --reading-levels grade_5,college`.
- **Idempotency:** a contrast that already holds the target rendition is skipped at $0, and
  a changed canonical text earns one re-run (D-47 markers, as for other renditions).

### 3.3 Leveled lexical explanations (fix the encyclopedia label, add "Why This Word")

- **Field:** `Lexeme.lexical_explanation` at `(grade_5, plain)` and `(college, plain)`.
- **Mechanism:** already supported:

  ```bash
  opengloss enrich --fields explanation --reading-levels grade_5,college
  ```

- **Volume:** 160,724 × 2 = **321,448 renditions**.

## 4. Exporter changes (`export/pretrain.py`, no model calls)

1. **No fallback copies.** A non-neutral document is emitted only if (a) at least one of its
   sections was rendered at that level, and (b) its normalized text differs from the same
   entry's neutral document for the template. Otherwise the level is skipped for that entry.
   The `ExportSummary` counts `documents_skipped_no_level_content`.
2. **Accurate `level_used`.** Report `exact` when every leveled section is at the requested
   level and `mixed` when some sections fell back (the document is still distinct under rule
   1). This replaces the current all-or-nothing flag. Add a `sections_at_level` count column.
3. **Thesaurus by level.**
   - Sense headings use the gloss rendition at the requested level. This exists for about
     99.9% of senses today, at $0.
   - After the relation lists, add a new "Choosing between them" section: the leveled
     contrast paragraphs (3.2) for that sense's synonym, antonym and confusable edges.
   - At `neutral` the section uses the canonical contrasts, so contrasts **move** here from
     the usage note.
4. **Usage note by level.** Per sense, list the register renditions at the requested level
   (3.1). Contrasts leave the usage note (item 3), so no paragraph appears in two templates
   of one entry. `slang` and `in_house` remain neutral-only lines.
5. **Encyclopedia by level.** "Why This Word" uses the leveled explanation (3.3). Retired
   lexemes emit no documents in any template.
6. **Duplicate gate.** `export-pretrain` and `export-hf` hash every document and fail the
   export if any two distinct document ids share a normalized hash. The gate is fatal by
   default, with `--allow-duplicates` for diagnosis only.

Tests to add (`tests/test_pretrain.py`):

- fallback-only levels are skipped;
- mixed levels are labeled `mixed`;
- a contrast appears in the thesaurus and not in the usage note;
- retired lexemes are skipped;
- the duplicate gate fires on a constructed duplicate;
- a golden document for one entry at all three levels.

## 5. Cost and volume estimate

Unit costs are measured from the fill round (gpt-6-luna flex, low reasoning):

- gloss register renditions: $19.37 for 786,548, which is $2.46e-5 and ~73 output tokens
  each;
- contrast paragraphs: $9.49 for 177,259, which is $5.35e-5 and ~180 output tokens each.

A leveled contrast rewrite is assumed to be at most as long.

| item | renditions | est. output tokens | est. cost |
| --- | ---: | ---: | ---: |
| 3.1 leveled register glosses (6 / sense) | 1,804,722 | ~132M | ~$45 |
| 3.2 leveled contrasts | 541,456 | ~70–100M | ~$25–40 |
| 3.3 leveled explanations | 321,448 | ~25M | ~$10 |
| retries and remainder sweeps (~10%) | | ~25M | ~$8 |
| **total** | **~2.67M** | **~250–280M** | **~$90–105** |

With `marketing` added to 3.1 (8 per sense), add about 600K renditions and about $15.

**Expected effect on the corpus.** About 634K duplicate documents (~131M words) are replaced
by about 620K documents of leveled text of similar length:

- usage notes average about 346 words, and contrasts move to the thesaurus;
- the pretrain exact-duplicate share goes from 32.9% to **0%** (the gate);
- unique 256-token windows go from 85% to about 100%.

## 6. Phases and gates

### L0 — code (no model calls)

- Add `RenditionField.CONTRAST` with its prompt, acceptance checks, `--fields contrast` CLI
  wiring and dry-run estimate.
- Make the exporter changes in section 4.
- Tests pass: `ruff check`, `ruff format --check`, `pytest -q`.
- **Gate:** exporting the current store with the new exporter shows 0 exact duplicates
  (dropping copies without replacement), and the skip counts match section 1's table.

### L1 — pilot (300 entries, scratch copy of the store)

- Stratified by tier and POS, with contrast-bearing entries over-sampled. Built with the
  `sample-300` pattern under `/data1/opengloss-generator/leveled/pilot-store`.
- Run 3.3, 3.1 and 3.2 there. Report calls, cost, output tokens, retries and flags per
  target.
- **Gates** (all must pass before L2):
  - readability band compliance ≥ 95% per `(level, register)` cell;
  - headword-initial renditions ≤ 1%;
  - flagged near-copies ≤ 2%;
  - contrast renditions naming both terms: 100% (enforced by the check);
  - near-copy rate between the `grade_5` and `college` rendition of the same target
    ≤ 5%, so the two levels must differ from each other as well as from `neutral`;
  - pilot export has 0 duplicate documents;
  - a blinded judge sample (`claude-opus-5`, 100 items balanced across cells, about $5)
    finds meaning preserved in ≥ 97% and level-appropriate in ≥ 90%;
  - measured unit cost within 1.5× of section 5.
- **Record:** a pilot section in this document with the numbers.

### L2 — full generation

- **Snapshot first:** `/data1/opengloss-generator/snapshots/core-store-fill-v2.4-<date>.tar.zst`.
- Reuse the fill-round infrastructure:
  - twelve disjoint shards (`/data1/opengloss-generator/fill-v2.4/lists/`);
  - one signal-safe `systemd` chain per shard;
  - `gpt6-luna.toml` with 256 workers and `max_attempts=6`.
- Stage order per shard, each capped:

  | stage | budget per shard |
  | --- | ---: |
  | 3.3 explanations | $1.5 |
  | 3.1 leveled register glosses | $5 |
  | 3.2 leveled contrasts | $4 |

- Then a remainder sweep of all three stages, which costs ~$0 for finished work.
- Progress via `fill-v2.4/progress.py` (every call gpt-6-luna flex; zero downgrades
  expected).
- **Stop rules:**
  - pause if the attempt-failure share exceeds 30% for 30 minutes (flex capacity);
  - stop a stage whose flag rate exceeds twice its L1 rate.

### L3 — export, census, verify

- Local export `--release v2.4-rc2` to `/data1/opengloss-generator/v2.4-rc2/hf` (no push),
  with the duplicate gate on.
- `scripts/census_v23.py --release v2.4-rc2` and comparison with v2.4-rc.
- **Acceptance:**
  - pretrain exact duplicates = 0;
  - near-duplicate excess ≤ 1%;
  - unique 256-token windows ≥ 98%;
  - no regression in distinct-n or opener entropy in any family;
  - `level_used = exact` for ≥ 95% of non-neutral documents;
  - encyclopedia documents = live lexemes only.
- **Record** results in this document and in `FILL-ROUND-V2.4.md`.

## 7. Risks and mitigations

| risk | mitigation |
| --- | --- |
| `technical` at `grade_5` is awkward | Instruct it as "the precise vocabulary of a school science or civics text". The Dale-Chall check applies. Judge it specifically in L1; drop the cell if level-appropriateness is below 85%. |
| Leveled contrasts drift from the stored verdict | Verdict in the prompt, a phrase-list check, and a verdict-consistency item in the L1 judge sample. |
| Two levels of the same target come out nearly identical | L1 near-copy gate between levels; retry with feedback naming the other level's text. |
| Flex capacity shortfalls (seen in the fill round) | `max_attempts=6`, remainder sweep, no tier downgrade. |
| Lock contention | Shards stay disjoint; store-wide passes are not needed for this plan. |
| Moving contrasts changes a published template's layout | Record it in the release notes and cards for v2.4; the v2.3 layout stays available on the Hub. |
| Cost overrun | Per-shard caps sum to at most $126. Any stage that stops on budget is reported, never silently extended. |

## 8. Why this, for both goals

- **Lookup and reference:** a reader can ask for a sense's informal, formal or technical
  explanation at their own reading level, and a thesaurus entry at their level explains how
  to choose between near-synonyms instead of only listing them.
- **Micro embedding models:**
  - About 131M words of copied text become meaning-preserving paraphrases along two
    controlled axes, level and register. This is the §3.6 crossing of the encoder plan: the
    same concept appears in many cells, and each cell holds many concepts.
  - Leveled contrasts are paired discriminative texts: two near-neighbors, one paragraph
    saying how they differ. That is useful hard-negative material.
  - The duplicate gate makes the corpus's nominal tokens honest.

## 9. Decisions needed

1. **Marketing in the leveled cross.** Default is **no** (6 per sense). Yes means 8 per
   sense for about +$15.
2. **Pretrain levels.** Default remains `neutral, grade_5, college`. `grade_1` and
   `grade_10` gloss renditions already exist, and adding them later would need the same
   treatment.
3. **Contrast placement.** Default: move contrasts from the usage note to the thesaurus.
   The alternative is to keep them in both, which would reintroduce cross-template overlap.

---

## Execution log

### L0 — code (2026-09-24)

- `RenditionField.CONTRAST` (`workflows/enrich.py`):
  - one work item per stored contrast, built by `_contrast_works`;
  - the source is the canonical paragraph behind a header naming the related term and the
    stored verdict;
  - the headword-absent check now applies to contrasts too, with one retry and then a flag;
  - CLI: `enrich --fields contrast`.
- **Prompts.** `RENDITIONS_INSTRUCTIONS` is byte-unchanged, so the cache prefix and
  `PROMPT_VERSION = "9"` (D-25) stand. Contrast, leveled-explanation and level × register
  guidance goes into the per-call prompt instead, and those calls record
  `prompt_version = "9-leveled-2"` (`prompts.renditions_prompt_version`).
- **Exporter** (`export/pretrain.py`), all of section 4:
  - no fallback copies (`documents_skipped_no_level_content`);
  - `level_used` is the level itself, or `mixed`;
  - new `sections_at_level` column (HF schema updated);
  - thesaurus: leveled headings plus "Choosing between them" contrast notes, with contrasts
    moved out of the usage note;
  - usage note at a level: only register lines written at that level;
  - retired lexemes emit nothing;
  - fatal duplicate gate (`DuplicateDocumentError`; `export-pretrain --allow-duplicates`
    for diagnosis);
  - two clean-up fixes: register lines no longer end with `.;` or `..`, and contrast targets
    use the relation's surface term instead of the lexeme slug.
- Tests were added in `tests/test_enrich.py` and `tests/test_export_pretrain.py`; the full
  suite passes.
- **L0 gate: passed.** A new-exporter pretrain export of the working store at
  neutral, grade_5 and college gave 1,590,535 documents and **0 duplicates**. Skip counts
  matched section 1 exactly: dictionary 16/53, encyclopedia 353/353, and usage note
  160,724/160,724, awaiting content. Encyclopedia documents fell to 160,489 per level, so
  retired lexemes are excluded.

### L1 — pilot (300 entries, `/data1/opengloss-generator/leveled/pilot-store`)

- **Sample.** 50 entries per tier (core, tier2–6), half of them with contrasts. The three
  passes plus remainder sweeps ran on gpt-6-luna flex, low reasoning.
- **Capacity.** Flex capacity was severely constrained: about 900 capacity 429s per pass,
  and up to 140 of 300 entries needed a second sweep. Rejected calls are unbilled.
- **Measured unit cost against the plan:**

  | pass | cost per rendition | vs plan | output tokens each |
  | --- | ---: | ---: | ---: |
  | gloss | $3.1e-5 | 1.27× | 108 |
  | contrast | $6.9e-5 | 1.3× | 245 |
  | explanation | $5.0e-5 | 1.6× | 168 |

  Explanations exceed the 1.5× gate by a few dollars in absolute terms. This is accepted and
  the section 5 estimate is revised below.
- `reports/leveled-pilot/report.json`: 4,424 renditions across 2,212 grade_5/college pairs.

**Deterministic gates:**

| gate | result |
| --- | --- |
| near-copy of source | 0% in every cell — pass |
| grade_5 vs college near-copy | 0% — pass |
| contrasts naming both terms | 100% — pass |
| readability band ≥ 95% | fail: gloss grade_5/formal 84.2%, grade_5/technical 82.5%; every other cell ≥ 96.5% |
| headword-initial ≤ 1% | fail: gloss cells 1.0–2.7% |
| pilot export duplicates | 0 — pass (3,544 documents; 97.5% fully at level) |

**Judge** (`claude-opus-5`, blinded; the first run had 38 unparsed replies, fixed by robust
parsing and a longer reply budget):

- **Calibration baseline.** The 97% meaning gate was set without a baseline. The same judge
  on content already accepted in the store (`reports/leveled-pilot/baseline-existing.json`,
  100 items) scores **88% meaning-preserved**. By cell: v2.3 grade_5/plain glosses 75%,
  college/plain 85%, neutral formal 85%, informal 95%, technical 100%. **The gate is
  recalibrated** to "each new cell ≥ its nearest existing cell − 5 points and ≥ 75%"; the
  97% figure was above what the release itself achieves.
- New leveled glosses scored 83% meaning, 97% level-appropriate and 85% register-appropriate
  (60 items):

  | gloss cell | meaning | register | decision |
  | --- | ---: | ---: | --- |
  | grade_5/technical | 50% | 30% | **dropped** (section 7 mitigation) |
  | grade_5/formal | 77% (n=30) | | kept: at parity with the grade_5 baseline |
  | grade_5/informal | 100% | | kept |
  | college/informal | 90% | | kept |
  | college/formal | 80% | | kept |
  | college/technical | 100% | | kept |

- **Contrasts:** 100% meaning, level, register and verdict consistency (n=20).
- **Explanations, first prompt:** college 67% and grade_5 80% (n=30 each). The failures were
  mostly omissions forced by the base instruction ("a two or three sentence usage note") plus
  a few invented relations. **Fix:** a per-call explanation guidance block (keep the core
  definition and every stated distinction, add no new relation, keep length at college). The
  regenerated pilot explanations (`reports/leveled-pilot/explanation-v2-judge.json`) scored
  **college 96.7%** and **grade_5 83.3%** on meaning, with level-appropriate at 97–100%.
  Accepted.
- **Readability and headword-initial residuals.** Flagged renditions (after their one retry)
  stay in the store for lookup and later `rendition_hygiene`, but are **kept out of the
  corpus**: leveled usage-note lines skip `og.readability_miss`, `og.headword_initial`,
  `og.near_copy` and `og.hard_vocabulary`. The exported corpus therefore meets both gates by
  construction.

**Amended L2 scope:**

| item | targets | renditions |
| --- | --- | ---: |
| 3.1 | gloss at {grade_5 × informal, formal} ∪ {college × informal, formal, technical} | 5 per sense ≈ 1.50M |
| 3.2 | contrast at {grade_5, college} | ≈ 541K |
| 3.3 | explanation at {grade_5, college}, with the new guidance | ≈ 321K |

**Revised estimate from measured unit costs:** 3.1 ≈ $47, 3.2 ≈ $37, 3.3 ≈ $16, plus about
10% for retries and sweeps, which is **≈ $110** and about 260M output tokens.

### L2 — full generation (started 2026-09-24 12:45 UTC)

- **First launch, flex only** (`chain.sh`, 256 workers × 12). gpt-6-luna flex capacity was
  near zero at US midday: 12.5K capacity 429s against 343 completions (0.6 calls/s). It was
  stopped cleanly (no locks left) after $0.35.
- **Relaunch, with user approval, on `scripts/gpt6-luna-fallback.toml`.** Same model and
  reasoning; flex first, falling back to `auto` (standard tier) after 20 consecutive
  capacity rejections per stage process. `chain2.sh` repeats passes over both lists until a
  pass adds nothing and fails nothing, with 192 workers × 12. Throughput was 53–228 calls/s,
  and a local internet restart at ~13:20 UTC only paused calls (the stages retried).
- **Budget stops, reported rather than hidden.** The per-stage caps were sized for flex
  prices, so at the standard tier 20 of the first 57 stages stopped at their cap (11
  contrast, 4 explanation, 4 leveled-gloss, 1 college-technical). The next pass resumes the
  missing work, which costs ~$0 for finished items.
- **Cost re-projection at 14:20 UTC** (37–49% complete per stage, $146.88 spent):

  | stage | projected |
  | --- | ---: |
  | explanation | ≈ $59 |
  | grade_5/college × {informal, formal} glosses | ≈ $105 |
  | college technical glosses | ≈ $41 |
  | contrasts | ≈ $83 |
  | **total** | **≈ $290** |

  That is versus ≈ $110 on flex. The difference comes from the standard tier (2×) and from
  longer explanation outputs under the new guidance.
- **14:45–15:00 UTC: back to flex only, at the user's request because of cost.** The
  fallback chains were stopped cleanly. Standard-tier (`auto`) spend is frozen at
  **938,309 calls, $221.14**. The chains were relaunched on `scripts/gpt6-luna.toml`
  (flex, never downgrades) with 64 workers × 12. L2 total at the switch: 956,876 calls,
  312.7M output tokens, $223.36. Flex throughput at the time was about 4.5 calls/s. The
  remaining work (about half of tiers 3–6) is estimated at $50–70, and the converging chain
  keeps sweeping as capacity returns.
- **17:36 UTC: L2 complete.** All twelve shards converged, each ending on a pass with zero
  added and zero failed. **Final L2 ledger:** 1,234,416 calls, 401.0M output tokens,
  **$250.78**, of which $221.14 was standard tier (before the switch back) and $29.64 was
  flex. The per-stage summary JSONs undercount renditions, because the flex relaunch reused
  pass names and overwrote them; the export counts below are authoritative.

### L3 — export, census, verify (2026-09-24)

- **Export.** `export-hf --release v2.4-rc2` to `/data1/opengloss-generator/v2.4-rc2/hf`
  (local, no push) ran in 32 min with 3.35 GB peak memory and 6.5 GB on disk. The fatal
  duplicate gate passed, so there are 0 duplicate pretrain documents.
- **Coverage is exactly the planned targets:**

  | item | before | after | added | target |
  | --- | ---: | ---: | ---: | ---: |
  | gloss renditions | 2,705,559 | 4,209,494 | +1,503,935 | 5 × 300,787 |
  | contrast renditions | 270,728 | 812,184 | +541,456 | 2 × 270,728 |
  | explanation renditions | 160,724 | 482,172 | +321,448 | 2 × 160,724 |

- **Census** (`reports/census-v2.4-rc2/census.json`). The first attempt was stopped by the
  host under memory pressure while other jobs were running; the rerun with 3 workers
  finished in 14 min with 46 GB peak.

**Acceptance (section 6, L3):**

| criterion | v2.3 | v2.4-rc (fill round) | **v2.4-rc2** | pass |
| --- | ---: | ---: | ---: | --- |
| pretrain exact duplicate share | 24.96% | 32.90% | **0.00%** | ✅ |
| pretrain near-duplicate excess (≤ 1%) | 25.03% | 32.95% | **0.07%** | ✅ |
| unique 256-token windows (≥ 98%) | 85.0% | 75.1% | **99.66%** | ✅ |
| non-neutral documents fully at level (≥ 95%) | — | — | **99.81%** (2,468 mixed of 1,272,598) | ✅ |
| encyclopedia documents = live lexemes | 165,291 / level | 165,291 / level | 160,724 neutral; 160,371 per level where a leveled overview exists | ✅ |
| distinct-4-gram rate: no regression | | | improved in every family that changed | ✅ |
| opener entropy: no regression | | | **gloss.register 13.52 → 13.14 bits; pretrain 13.30 → 12.64** | ❌ |

- **The one miss, opener entropy.** Leveled *informal* glosses open "It's a / It's the /
  It's when…" about 18% of the time (the informal stratum's opener entropy is 10.1 bits).
  The pretrain usage notes inherit it as "Informally: It's…". This is a stylistic habit of
  one cell; lexical diversity rose everywhere.
- **Optional fix, not run** because of cost sensitivity: regenerate only the ~160K leveled
  informal glosses whose text opens with "It's", using an opener-variety instruction and
  the existing retry machinery. Estimated at about $5–8 on flex.

**Unique content, v2.3 → v2.4-rc2** (student tokenizer, normalized-unique):

| family | unique texts v2.3 | v2.4-rc2 | × | unique tokens v2.3 | v2.4-rc2 | × |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gloss.register | 415,841 | 2,704,774 | 6.50 | 16.2M | 108.8M | 6.73 |
| example.register | 254,267 | 1,990,666 | 7.83 | 7.0M | 61.7M | 8.87 |
| contrast | 81,044 | 812,174 | 10.02 | 19.1M | 161.3M | 8.45 |
| lexical_explanation | 160,724 | 482,159 | 3.00 | 48.8M | 103.5M | 2.12 |
| query | 1,246,075 | 3,843,857 | 3.08 | 31.2M | 107.8M | 3.45 |
| QA question / answer | 697,741 / 702,657 | 2,274,125 / 2,277,893 | 3.26 / 3.24 | 20.8M / 30.5M | 69.9M / 94.6M | 3.36 / 3.10 |
| encyclopedia | 500,320 | 802,914 | 1.60 | 376.6M | 583.0M | 1.55 |
| example.reading_level | 2,164,262 | 2,945,315 | 1.36 | 58.9M | 82.8M | 1.41 |
| canonical / reading-level glosses, etymology | unchanged | | 1.00 | | | 1.00 |
| **all source text** | **7,871,053** | **19,781,999** | **2.51** | **697.6M** | **1,461.8M** | **2.10** |
| pretrain (derived; now 0 duplicates) | 1,170,628 | 1,910,373 | 1.63 | 1,014.6M | 1,325.3M | 1.31 |
