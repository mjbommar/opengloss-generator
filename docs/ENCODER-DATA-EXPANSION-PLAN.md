# OpenGloss encoder-data expansion plan

**Date:** 2026-09-23  
**Status:** prospective research plan; no generation or model result is implied  
**Consumer:** `../opengloss-embedding`  
**Release boundary:** OpenGloss v2.3 remains immutable. New material is experimental until a
separately named release passes the gates below.

## 1. Why this work is next

The best replicated OpenGloss encoder has 21,308,033 parameters and emits 256-dimensional vectors.
On SHELF v0.3.1 validation it scores `.430668` family-balanced overall and `.579488` retrieval
nDCG@10. Under the same validation protocol, raw mean-pooled MLM backbones score:

| model | parameters | width | overall | retrieval |
| --- | ---: | ---: | ---: | ---: |
| OpenGloss best replicated mean | 21.3M | 256 | **.430668** | **.579488** |
| BERT-base raw MLM | about 110M | 768 | .340293 | .470637 |
| RoBERTa-base raw MLM | about 125M | 768 | .251389 | .304857 |
| ModernBERT-base raw MLM | about 149M | 768 | .287724 | .389258 |
| MiniLM-L6-v2, sentence-trained, common-width route | about 22.7M | 256 | **.454690** | **.582703** |

OpenGloss therefore already creates substantially better frozen embedding geometry than much
larger raw MLM backbones. Architecture capacity is not the first-order failure. The remaining
MiniLM gap is only `.003215` retrieval but `.024022` overall. Family decomposition localizes the
gap mainly to broad classification and graded similarity rather than basic retrieval.

The working hypothesis is:

> OpenGloss has abundant structured lexical supervision but less independent linguistic and
> discourse diversity than sentence-trained competitors. Multiple natural realizations of the
> same grounded concepts, followed by realistic query/neighborhood supervision, can improve broad
> geometry without surrendering OpenGloss's lexical precision.

This is not permission to generate undifferentiated volume. A complete v2.3 pretraining epoch is
already about 594M exported tokens and the encoder program has consumed much larger attended-token
budgets through repeated views and curricula. The experiment must distinguish new information from
more tokens describing the same facts in the same synthetic voice.

## 2. What v2.3 already provides

Do not rebuild existing mechanisms under new names. Schema v3 and the v2.3 release already include:

- typed, sense-resolved relations and complete lexical graph views;
- gloss, example, encyclopedia and lexical-explanation `Rendition` objects;
- reading-level and register variants;
- eight query styles per sense;
- seven grounded QA types at multiple difficulties;
- pair, triple and complete grade-3/2/1/0 qrel/listwise exports;
- relation contrasts, confusables and hard-negative sources;
- deterministic pretraining templates;
- provenance, QA assessments, near-copy/filler checks and immutable exports;
- a multi-writer pilot across Luna, Haiku, Gemini and Qwen.

The writer pilot is directly relevant. Its four-way writer attribution reached 66% versus 25%
chance, but uneven alphabetic/topic coverage confounded that estimate. Qwen leaked prompt labels,
Gemini failed one structured-output schema, DeepSeek could not satisfy native structured output,
and per-writer budgets produced unequal content coverage. A new pilot must use the same fixed
entries in every arm, hide control labels from prose, and measure writer identity independently of
topic.

## 3. Target data families

### 3.1 Purpose-diverse single-concept documents

For the same sense or concept, generate genuinely different communicative realizations rather than
surface paraphrases:

1. concise dictionary explanation;
2. conventional encyclopedia passage;
3. textbook explanation with prerequisites;
4. plain-language explanation for a novice;
5. technical or professional treatment;
6. historical or chronological narrative where grounded facts support it;
7. compare-and-contrast explanation;
8. practical worked example or scenario;
9. misconception correction;
10. concise reference answer.

Purpose is a separate experimental label. Do not overload `Register`: style, audience, genre and
communicative purpose are different variables. During the pilot, place these records in an
additive sidecar/export schema so existing v3 `Lexeme` payloads remain valid.

### 3.2 Headword-free realizations

Generate passages that express the target meaning without using the headword, aliases or trivial
morphological variants. These records force semantic rather than string-overlap alignment.

Required checks:

- case-folded headword, alias and inflection absence;
- no prompt-label leakage;
- a stored link to the target sense and grounding sources;
- an independent entailment/grounding check;
- a paired named treatment describing the same concept, for controlled contrast.

### 3.3 Multi-concept and discourse documents

Produce coherent 512-, 1,024- and, where evidence supports it, 2,048-token documents that connect
multiple resolved senses. Supported structures include:

- category and instance;
- cause and consequence;
- chronology;
- competing explanations;
- whole and part;
- process steps;
- commonly confused concepts;
- analogy with an explicit limit;
- a small topical survey joining several graph neighbors.

Every included claim must resolve to one or more stored source renditions. Do not permit the writer
to add outside facts merely to make a longer article. Long documents must contain real discourse—
transitions, anaphora and cross-paragraph dependencies—not concatenated independent definitions.

### 3.4 Natural query scenarios

Extend current query coverage with realistic information needs:

- terse and underspecified search queries;
- full questions;
- novice and expert formulations;
- troubleshooting or decision questions;
- comparison questions;
- queries that imply but do not name the concept;
- multi-hop queries grounded in two or more linked senses;
- queries containing plausible irrelevant details;
- queries with a constraint such as audience, jurisdiction, time or domain when supported.

Each query must be attached to a complete neighborhood, not emitted as an isolated pair.

### 3.5 Complete graded neighborhoods

For each selected query, construct multiple candidates with explicit roles:

- grade 3: direct and complete answer, often with more than one valid positive;
- grade 2: correct supporting or paraphrased passage;
- grade 1: topically related but incomplete or adjacent passage;
- grade 0-hard: same topic/register with the decisive semantic fact wrong or absent;
- grade 0-easy: unrelated passage.

Candidate construction should preferentially reuse independently generated documents, resolved
graph neighbors and stored contrasts. An LLM may adjudicate grades, but it must not invent candidate
text and grade that same text in one unreviewed call. Store the grade rationale separately from the
student-facing record.

### 3.6 Classification-crossed renditions

The raw-BERT comparison shows that broad classification remains a weakness. Cross the same concepts
with independently controlled labels for:

- topic/domain;
- audience;
- register/formality;
- genre/form;
- communicative purpose;
- technical depth;
- temporal and geographic scope when grounded.

The same concept must appear in multiple cells, and each cell must contain many concepts. Otherwise
a classifier can infer topic from concept identity or writer from the subset it received.

## 4. Experimental record schema

Before implementation, freeze a strict additive schema. A document record should carry at least:

```text
document_id                 deterministic
lexeme_ids                  one or more
sense_ids                   one or more resolved live senses
purpose                     controlled enum
audience                    controlled enum
register                    existing controlled value
genre                       controlled enum, separate from register
headword_policy             named | implicit | either
length_band                 short | 512 | 1024 | 2048
text                        generated prose
grounded_in                 rendition ids and/or contrast ids
writer_model                immutable model id
writer_revision             when the provider exposes one
provider                    direct or routed provider
prompt_version              immutable
generation_seed             explicit
source_digest               digest of every grounding input
assessment                  factual, grounding, style and hygiene results
partition                   train | development | internal_test
```

A query-neighborhood record should add:

```text
query_id, query, query_style, target_sense_ids,
candidates[{document_id, grade, source_kind}],
grade_rationale_id, neighborhood_digest
```

Unknown fields remain forbidden. Generation markers must be digest-keyed and idempotent. All model
calls continue through `StageRunner`; exact input/output tokens, cache tokens, cost, service tier,
latency, failures and retries remain provenance, not console-only information.

## 5. Generator policy

### 5.1 Writers

Use at least two model families in every scaled treatment. The first pilot should compare:

- the current Luna policy as the same-style control;
- Luna plus Terra, or the current account's closest verified higher-capability sibling;
- one non-OpenAI family that passes the exact structured-output acceptance;
- a mixed-writer allocation.

Resolve model availability and pin exact identifiers immediately before the pilot. Do not write a
future model name into provenance until the provider API confirms it. A more expensive model should
be reserved for difficult multi-concept documents, adjudication and repair rather than used for all
bulk prose by default.

### 5.2 Same-content allocation

Every writer arm receives the identical stratified list of senses, purposes and length bands.
Budget exhaustion may not decide which headwords a writer sees. If an arm cannot finish, compare
only the shared completed block and report the missingness.

### 5.3 Prompt and style controls

- Never expose literal enum labels such as `grade_10` in a position likely to leak into prose.
- Vary rhetorical structure explicitly; temperature alone is not a diversity mechanism.
- Require concrete openings and ban a measured list of frequent synthetic fillers.
- Do not ask one call to write many supposedly independent renditions.
- Store a prompt-family identifier so prompt diversity is distinguishable from writer diversity.
- Use blinded judges: do not reveal writer, treatment or intended promotion result.

## 6. Intrinsic quality and diversity evaluation

All metrics are computed on a frozen, stratified sample and by partition. Report distributions and
worst strata, not just corpus means.

### 6.1 Grounding and factual integrity

- deterministic citation resolution: 100%;
- no missing or retired sense references: 100%;
- claim-support judge pass target: at least 97%;
- contradiction rate target: at most 1%;
- unsupported-material rate target: at most 2%;
- independent re-judge a fixed 10% sample with a different model family;
- manually inspect at least 100 records balanced across writers/purposes before scaling.

These are pilot gates, not retrospectively adjusted aspirations. A failed stratum is repaired and
replayed; it is not averaged away by easy definitions.

### 6.2 Hygiene

- exact duplicate text: zero across distinct document ids;
- prompt/control-label leakage: zero;
- headword-free compliance: at least 99%;
- invalid Unicode, empty text and schema violations: zero;
- near-copy rate against grounding text: at most 2% per purpose;
- repeated high-frequency 4-grams and sentence openers reported per writer and purpose;
- readability and length-band compliance reported, with no silent truncation.

### 6.3 Diversity

Measure on equal-sized, same-concept samples:

- content-word Jaccard and embedding similarity against source and sibling renditions;
- distinct n-grams at matched sample size;
- sentence-opener entropy;
- syntactic-pattern and document-structure distributions;
- writer-attribution accuracy with folds grouped by concept;
- purpose/audience/register classification accuracy;
- mutual information between writer and topic/purpose;
- semantic coverage of source claims.

The writer-attribution target is not necessarily chance: factual grounding creates legitimate
shared vocabulary. Require a material reduction from the corrected same-concept Luna-only or
single-writer control, with no grounding regression. The old 66% figure is context, not a valid
gate, because its coverage was confounded.

### 6.4 Effective information, not nominal tokens

For every corpus report:

- tokenizer-specific attended tokens at contexts 256, 512 and 1,024;
- unique normalized texts and unique sliding windows;
- exact and MinHash-near duplicate rates;
- tokens per unique source claim;
- average repetitions of each sense, fact and document template;
- entropy and concentration by domain, purpose, writer and length;
- coverage of lexemes, senses and graph components.

Nominal token count alone is never the expansion criterion.

## 7. Phased generation program

### Phase G0 — census and design freeze

No paid generation.

1. Census current v2.3 documents, queries and neighborhoods using the measures above.
2. Quantify which purposes, domains, query styles, lengths and grade cells are actually sparse.
3. Measure corrected writer attribution on an equal-content sample.
4. Freeze schemas, partitions, prompts, model identities, budgets and result paths.
5. Confirm that SHELF, MTEB, NanoBEIR and other evaluation text is never sent to a generator.

Deliverable: one machine-readable census plus a reviewed Markdown scorecard.

### Phase G1 — 300-entry systems acceptance

Use the existing sample-300 pattern, but stratify across domain, polysemy, frequency, entity type
and document length. Exercise every new schema and every writer/purpose cell. This phase proves:

- structured output and retry behavior;
- deterministic ids and byte-identical replay;
- idempotent resume after interruption;
- grounding and headword-free checks;
- cost and latency accounting;
- exports load through `datasets` and join back to source senses.

G1 is not a model-quality experiment.

### Phase G2 — 2,000-sense causal pilot

Use the same 2,000 senses in every arm. Materialize four corpora:

| arm | added material | purpose |
| --- | --- | --- |
| V | additional current-style v2.3 renditions | volume control |
| D | purpose-diverse, headword-free and multi-concept generated documents | diversity treatment |
| R | matched tokens of real Wikipedia/FineWeb-Edu text | natural-text control |
| D+R | 50/50 generated-diverse and real text | combined treatment |

Construct query neighborhoods from the same frozen document pool after prose generation. Keep
student attended tokens, optimizer updates, architecture, tokenizer, context, seed and existing
OpenGloss replay identical. This is the experiment that separates diversity from sheer volume.

### Phase G3 — 10,000-sense confirmation

Promote at most one generated condition and the real-text control from G2. Repeat over 10,000
stratified senses and a second seed. Re-estimate intrinsic gates and downstream effects. Do not
change prompts or mixture after seeing model-quality results.

### Phase G4 — bounded production expansion

Only after G3 replication:

1. generate in immutable blocks with independent receipts;
2. quality-audit every block before it enters an export;
3. stop at predeclared 100M/400M/1B unique-attended-token boundaries;
4. measure marginal gain per dollar and per unique token at each boundary;
5. require a new contract to pass the next boundary.

Do not publish a new OpenGloss release merely because generation finished. Publish only after the
store, exports, cards, licenses, provenance and downstream evidence are terminal.

## 8. Encoder experiments

### 8.1 Primary 5M screen

Use exact-parameter-asserted 5M students for fast causal learning. Every arm starts from the same
committed parent and receives the same total attended tokens. Recommended curriculum:

1. immutable OpenGloss structured foundation parent;
2. matched broad-MLM continuation using V, D, R or D+R;
3. identical query/neighborhood stage;
4. identical low-LR, teacher-free OpenGloss consolidation.

The same-style V arm is essential. If D beats no-additional-data but not V at matched tokens, the
effect is dose, not diversity. Teacher-free and distilled conditions remain separate experiments.

### 8.2 Development metrics

Before any SHELF validation, compare on frozen internal and disjoint broad-text evidence:

- OpenGloss MRR and Recall@1;
- qrel nDCG and listwise ordering;
- graded-pair correlation/negative MSE;
- triplet accuracy;
- effective rank and singular-value concentration;
- broad-teacher correlation and neighbor recall;
- held-out FineWeb-Edu/Wikipedia MLM cross-entropy;
- per-domain and per-purpose slices;
- exact exposure, unique-window coverage and full compute/cost ledger.

### 8.3 Five-million-parameter promotion

Promote only a prospectively selected condition that, in two seeds:

- improves the balanced internal geometry point estimate;
- improves at least two of retrieval, listwise, graded and broad-teacher coordinates;
- does not reduce any core coordinate by more than `.01`;
- does not worsen held-out broad MLM cross-entropy by more than `.005`;
- retains effective rank within 1.0 of control;
- passes every intrinsic corpus gate;
- shows a gain over same-style volume V, not only over the original parent.

Thresholds must be frozen in the embedding repository before training. The values above are the
generator-side recommendation, not authority to overwrite an already committed encoder contract.

### 8.4 SHELF validation and 20M confirmation

After the internal winner is frozen, run one SHELF v0.3.1 validation comparison. Report every
family, not only the composite. A useful signal is either:

- overall gain at least `.005` with retrieval decline no worse than `.003`; or
- classification gain at least `.01` with overall non-decline and retrieval decline no worse than
  `.003`.

SHELF test remains sealed until a final replicated candidate exists. A 5M validation success
authorizes an exact matched 20M two-seed replication; it is not publication completion. At 20M,
the target is to close the MiniLM common-256D boundary of `.454690` overall and `.582703` retrieval
while preserving the substantially lower student-token/compute ledger.

### 8.5 Dose and mixture after a mechanism win

Only after the data mechanism wins at 5M and replicates at 20M should the encoder program test
100M, 400M, 1B and larger attended-token doses. Freeze mixture shares prospectively. Include:

- generated diverse only;
- real text only;
- generated + real text;
- generated + real text + structured OpenGloss replay;
- a final low-LR pure-OpenGloss consolidation.

This is the extended OpenGloss sandwich. It tests whether natural-language breadth and lexical
precision are complementary rather than forcing a choice between them.

## 9. Leakage, partitions and evaluation discipline

- Partition by connected lexical component before generation; every rendition/query/candidate
  derived from a component inherits that component's partition.
- Multi-concept documents may not cross partitions.
- A writer or judge receives source training records only, never evaluation documents or labels.
- Do not mine hard negatives from SHELF, MTEB, NanoBEIR or their labels.
- Do not use SHELF validation to choose prompts, writers, mixture shares, LR or checkpoints.
- Keep public/protected test labels closed through development.
- Report exact overlap audits against every evaluation corpus before release.
- Generated evaluation examples, if any, form a separate diagnostic and never replace independent
  public evaluation.

## 10. Cost and stopping rules

Each phase has its own cap and may not borrow from the next phase after a disappointing result.
Record cost for writing, judging, repair, export, teacher encoding and student training.

Stop or redesign when:

- a writer fails more than 5% of structured calls after the frozen retry policy;
- grounding or leakage gates fail twice after one repair pass;
- the diverse corpus is not measurably more diverse than same-style V at matched concepts/tokens;
- two guarded student milestones show no material improvement;
- gains disappear in the second seed;
- improvement comes only from longer context or larger width and not the common-256D product;
- cost per unit downstream gain is worse than simply adding verified real text.

Do not continue generation merely to reach a round token count.

## 11. Required artifacts

Every phase must leave:

1. committed strict schemas and prompt/model contracts;
2. immutable source lists and component partitions;
3. generation ledgers with exact cost and failure accounting;
4. intrinsic quality/diversity JSON plus a readable scorecard;
5. deterministic exports with hashes, row/token counts and load receipts;
6. overlap/firewall audit;
7. matched encoder contracts committed before training;
8. per-seed results and paired uncertainty;
9. a decision record stating promote, repeat or close;
10. an updated release note only if a new release is actually authorized.

Large stores, caches, generated shards and temporary artifacts belong under `/data1`, not in this
checkout, `/`, or `$HOME`. Only bounded fixtures, schemas, plans and summary evidence belong in git.

## 12. Immediate execution queue

1. Implement the G0 census specification without model calls.
2. Freeze the additive document and query-neighborhood schemas.
3. Build a component-safe, stratified 300-entry G1 list shared by every writer arm.
4. Run structured-output acceptances for currently available Luna/Terra and one external family.
5. Execute G1 under small fixed budgets; audit grounding, leakage, cost and replay.
6. Freeze the exact 2,000-sense G2 source list and four matched corpora.
7. In `opengloss-embedding`, commit the matched 5M contracts and numeric gates before training.
8. Run V, D, R and D+R in increasing calibrated GPU-hours, then replicate only the selected
   condition.

The near-term objective is not “make OpenGloss bigger.” It is to determine whether grounded
linguistic diversity supplies the broad sentence geometry that separates the current OpenGloss
encoder from MiniLM while retaining the lexical/retrieval advantage already demonstrated against
raw BERT, RoBERTa and ModernBERT.
