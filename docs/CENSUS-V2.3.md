# OpenGloss v2.3 census (G0, step 1)

**Date:** 2026-09-23  
**Plan:** [ENCODER-DATA-EXPANSION-PLAN.md](ENCODER-DATA-EXPANSION-PLAN.md) § 7, phase G0, step 1  
**Machine-readable census:** [`reports/census-v2.3/census.json`](../reports/census-v2.3/census.json)  
**Script:** `scripts/census_v23.py` (zero model calls; `uv sync --extra census --extra hf`)  
**Status:** descriptive census of the immutable release. It proposes no new data and records no
model result.

## Identity

- The census covers 16 datasets, 85 files and 3,917,257,976 bytes. Every file's SHA-256 matches the
  Hub LFS hash at the revision recorded in the JSON (`inventory.repos.*.hub_revision`).
- Tokenizer: `ogbert-tokenizer-16384.json`, the tokenizer of the `opengloss-embedding` students,
  SHA-256 `5c01d427…2623d`. Token counts below use it unless stated otherwise.
- Word-level diversity uses matched samples: 200,000 words per family, 50,000 per stratum, and
  bottom-k by text hash, so it is deterministic.
- Near-duplicates use MinHash over token 8-grams with 16×4 LSH and ≥0.8 signature agreement.
- **Replay:** a second complete run produced an identical JSON, excluding the `meta` block and
  per-family timings. The only difference found was the order of tied counts in
  `most_repeated`; that tie-break is now fixed, and the section was recomputed and re-verified.

## Headline findings

1. **The release has one writer.** 99.94% of v2 prose output tokens come from `gpt-5.6-luna`, and
   Claude Haiku wrote the other 0.06% (675 lexemes). The canonical glosses the renditions derive
   from come from two places. 257,863 senses were migrated from v1.3, written by an earlier model
   that v2.3 provenance does not record. 42,924 are WordNet 3.0 glosses, which are the only
   human-written text in the release.
   *Consequence:* the equal-content writer-attribution measurement (G0 step 3) cannot be run on
   v2.3 alone. It needs the old writer pilot's sample or new G1 data.
2. **Two-thirds of senses carry only dictionary-style supervision.** Queries, QA pairs, register
   variants of glosses and contrasts exist **only for core + tier-2** (104,150 senses, 34.6%).
   Register examples exist only for tier 2. Tiers 3–6 are 196,637 senses (65.4%) and have no
   generated query, no QA and no register variant; in qrels they appear only through gloss-pseudo
   queries. The register label is therefore perfectly predictable from tier (normalized MI = 1.0).
   That is exactly the confound § 3.6 of the plan forbids.
3. **Communicative purpose is narrow, and there is almost no long or multi-concept discourse.**
   - Of the plan's ten purposes, v2.3 fully covers only the dictionary and encyclopedia
     treatments.
   - Compare/contrast exists only as 81,046 two-sense synonym/antonym contrasts, averaging 236
     tokens, core/tier-2 only.
   - The concise reference answer exists only as QA answers averaging 43 tokens.
   - Historical treatment exists only as word etymology.
   - Textbook, novice and technical treatments exist only as ~40-token gloss rewrites.
   - Worked scenarios exist only as ~27-token single-sentence examples.
   - Misconception correction does not exist at all.
   - Every family except the encyclopedia is under 512 tokens at p99. The encyclopedia is 88.7%
     >512 but only 5.5% >1,024.
   - No document joins three or more concepts.
4. **Individual texts are clean and diverse. Monotony is in structure and openers, not duplication.**
   - Exact-normalized duplication is ≤1% in every prose family except canonical glosses (3.4%).
   - Near-duplication is effectively zero: 0.0 for the encyclopedia and ≤0.4% elsewhere.
   - Distinct-4-gram rates on matched samples are 0.88–0.99.
   - Low diversity sits in templated openings. 49% of `step_by_step` queries start "walk me
     through" (3.8 bits of opener entropy). Etymologies open with "an etymology trail…" 5% of the
     time.
5. **The pretraining corpus is 25% exact-duplicate documents.**
   - Its `level` fan-out copies text. Thesaurus documents at `grade_5` and `college` are
     byte-identical to `neutral` (307,012 documents), yet `level_used` labels them as
     level-specific.
   - Two-thirds of usage-note documents are neutral fallback copies (80,978).
   - `pretrain` is 1,162.6M student tokens nominal and 1,014.6M in unique documents. Only 85.0% of
     its 256-token windows are unique.
   - 54% of its characters are verbatim copies of source renditions.
   - The plan's "594M" is a `cl100k_base` count, not a student-tokenizer count.
6. **Component partitioning as the plan words it is impossible.**
   - The resolved sense graph has a giant component holding 92.7% of live senses (278,789).
   - Merged to lexemes, it holds 96.5%.
   - This matches the embedding repository's finding that "99.16% of graph identities occupy one
     connected component" (doc 222). Section 9 must adopt that repository's lexeme-family partition
     plus cross-partition edge firewall, not whole-component splits.
7. **Graded neighborhoods lack the roles the plan requires.**
   - Only 51.7% of the 1,446,320 listwise lists contain all four grades, and 45.7% have the
     profile 3/1/0 with no grade 2.
   - Only 13.9% have more than one grade-3 positive.
   - Grade 0 is not split into hard and easy.
   - Candidates are only glosses and encyclopedia entries. There are no passages, and no documents
     written independently of the query.
8. **Exposure follows tier.** A tier-2 sense appears in a median of 208 training-export rows, while
   tiers 5 and 6 appear in 42 and 40, and core in 78. The Gini coefficient over senses is 0.40.
   Across pairs, triples and qrels, 45.8M text slots contain only 4.31M distinct texts (10.6 uses
   each). Generic surname glosses head the list: "A surname of English origin used as a family
   name." appears 7,570 times.

## Scale and effective information

| family | rows | tokens | unique-text tokens | mean / p99 tok | >512 | exact dup | distinct-4 | opener H (bits) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gloss, canonical | 300,787 | 12.5M | 12.2M | 42 / 89 | 0 | 3.39% | .957 | 12.9 |
| gloss, reading level | 1,201,624 | 45.7M | 45.6M | 38 / 91 | 0 | 0.38% | .974 | 13.5 |
| gloss, register | 416,596 | 16.2M | 16.2M | 39 / 81 | 0 | 0.18% | .970 | 13.5 |
| example, reading level | 2,167,540 | 58.9M | 58.9M | 27 / 47 | 0 | 0.15% | .991 | 14.0 |
| example, register | 254,269 | 7.0M | 7.0M | 27 / 40 | 0 | 0.00% | .984 | 14.0 |
| encyclopedia | 500,320 | 376.6M | 376.6M | 753 / 1,155 | 88.7% | 0.00% | .982 | 14.2 |
| lexical explanation | 160,724 | 48.8M | 48.8M | 304 / 453 | 0.1% | 0.00% | .916 | 14.2 |
| contrast | 81,046 | 19.1M | 19.1M | 236 / 268 | 0 | 0.00% | .930 | 14.2 |
| etymology | 160,688 | 30.6M | 30.6M | 190 / 433 | 0.3% | 0.13% | .878 | 12.0 |
| query | 1,249,683 | 31.3M | 31.2M | 25 / 46 | 0 | 0.29% | .894 | 11.3 |
| QA question | 704,950 | 21.0M | 20.8M | 30 / 64 | 0 | 1.02% | .928 | 10.8 |
| QA answer | 704,950 | 30.6M | 30.5M | 43 / 91 | 0 | 0.33% | .974 | 13.0 |
| **source total** | | | **697.6M** | | | | | |
| pretrain (derived) | 1,560,030 | 1,162.6M | 1,014.6M | 745 / 2,435 | 40.2% | 24.96% | .923 | 13.3 |

- The encyclopedia is 54% of all unique source tokens.
- Pretrain's truncated attended tokens are 313.6M at context 256, 496.1M at 512 and 792.2M at 1,024.
- Of pretrain characters, 54.4% are verbatim copies of a source paragraph, 38.7% are deterministic
  recombinations (thesaurus lists, usage-note joins) and 7.0% are headers.

## Sparse cells (G0 step 2 input)

**Tier × supervision** (share of senses with zero):

| tier | senses | queries | QA | register variants | examples | relations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| core | 32,193 | 0% | 0.2% | 0% | 0.0% | 0.7% |
| tier 2 | 71,957 | 0% | 0.4% | 0% | 0.1% | 0.8% |
| tier 3 | 23,511 | 100% | 100% | 100% | 0.2% | 0.8% |
| tier 4 | 113,873 | 100% | 100% | 100% | 1.6% | 0.8% |
| tier 5 | 46,755 | 100% | 100% | 100% | 0.0% | 5.6% |
| tier 6 | 12,145 | 100% | 100% | 100% | 0.0% | 1.5% |

**Domain skew.** The core/tier-2 families (queries, QA, contrasts, register glosses) inherit that
subset's domain mix:

| domain | share of senses | share of queries | share of contrasts |
| --- | ---: | ---: | ---: |
| people and society | 12.1% | 19.1% | 22.6% |
| everyday life | 12.1% | 15.2% | 19.9% |
| science | 8.7% | 5.5% | 4.4% |
| education | 4.4% | 2.0% | 2.1% |
| nature | 7.7% | 6.1% | 3.8% |

**Query styles.** Every core/tier-2 sense has 12 queries over 8 styles. 77.8% are flagged
headword-free; only 5 of the 972,038 flagged queries actually contain the headword. The plan's
§ 3.4 scenarios are missing:

- troubleshooting and decision queries;
- multi-hop queries;
- queries carrying irrelevant detail;
- comparison queries as retrieval queries (comparison appears only as a QA type).

Opener entropy is lowest for `step_by_step` (3.8 bits), `example_based` (5.8) and `conversational`
(6.9).

**QA.** Type and difficulty are confounded: `factual` is 99% easy, `definition` is 95% easy, and
`reasoning` is 61% hard. Each pair cites 1.4 grounding ids on average.

**Listwise grades.** The overall grade counts are:

| grade | candidates |
| --- | ---: |
| 3 | 1.65M |
| 2 | 1.55M |
| 1 | 4.58M |
| 0 | 4.34M |

1.24M of the grade-1 candidates are encyclopedia entries. The 3/2/1/0 profile covers 51.7% of
lists, and 3/1/0 covers 45.7%.

**Triples.** The negatives are mostly easy (50.4%) or another sense of the same lexeme (41.5%).
Co-hyponym negatives are 7.4%, and confusable negatives are 0.05%.

## Implications for the plan (to review before the schema freeze)

1. **Separate coverage from diversity in G2.** Much of any "diversity" gain on tiers 3–6 could come
   from *first* coverage of queries, QA and registers, not from new linguistic variety. Arm V
   (same-style volume) should include Luna-style queries, QA and registers for the same tier-3–6
   senses as arm D. Otherwise D beats V on coverage alone.
2. **Match tokens on unique tokens.** Pretrain's 25% duplicate documents mean nominal tokens
   overstate information by 14.6% at the document level, and window-level uniqueness is 85%. The
   plan's matched-token rule (§ 7, G2) should match unique attended tokens under the student
   tokenizer and record `cl100k_base` only for comparison.
3. **Amend § 9.** Replace "partition by connected lexical component" with the embedding
   repository's lexeme-family partition ledger plus edge firewall. Multi-concept documents must not
   cross those partitions.
4. **Break the tier confound in § 3.6.** Stratify the G1/G2 source lists across tiers so every
   register, purpose and audience cell contains tier-3–6 senses, and report mutual information
   against tier and domain.
5. **Long discourse is the least covered target.** The 1,024–2,048-token and multi-concept
   documents in § 3.3 have essentially no v2.3 analogue: 5.5% of encyclopedia entries exceed 1,024
   tokens, and nothing joins three or more senses.
6. **Pretrain errata for the next release** (v2.3 stays immutable):
   - thesaurus `level_used` claims level-specific text that is identical to `neutral`;
   - encyclopedia documents at `grade_5` and `college` differ from `neutral` but carry
     `level_used = neutral`;
   - each template emits 165,291 encyclopedia documents per level, equal to the lexicon's row count
     including retired lexemes. That should be checked against the 160,724 live lexemes.

## Not covered by this census

These G0 steps remain:

- equal-content writer attribution (step 3; blocked on multi-writer data, see finding 1);
- the schema freeze (step 4);
- the SHELF/MTEB/NanoBEIR firewall and overlap audit (step 5).

This census opened no evaluation corpus.
