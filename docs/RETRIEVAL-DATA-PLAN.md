# Retrieval- and pretraining-data features (plan for the agent build, 2026-09-03)

Source of ideas: a survey of `~/projects/alea/alea-quality-model` (a legal-document
pipeline whose derived artifacts feed SFT/DPO/reward training). Read as retrieval-training
data rather than RLHF data, several of its tasks map onto the formats embedding and
reranker models (MS MARCO triples, TREC-style graded qrels, WiC pairs, doc2query) are
trained on — and OpenGloss's sense-tagged, graph-linked entries make most of the
expensive parts (hard negatives, relevance grades) free.

Target consumer: `../opengloss-embedding` (an encoder / embedding model). Two different
needs, scored on different axes:

* **more pretraining text** per entry (tokens per dollar), and
* **fine-tuning pairs/triples/graded lists** (pairs per dollar; output tokens are few).

## Non-negotiables (every feature)

1. Schema v3 conventions: derived positional ids, `Renditions[T]`, provenance records,
   D-47 digest-keyed idempotence markers with the 2-attempt bound. Read
   `docs/SCHEMA-V3.md`, `docs/DECISIONS.md` (D-1…D-53), `src/opengloss_generator/schema.py`.
2. Model calls go through `StageRunner` (`stages.py`) with a `StageName` and a
   `ModelPolicy` in `config.py`; `NativeOutput(strict=True)`; static instructions first,
   volatile input last, per-stage prompt cache key; budget guard reserves at
   `expected_output_tokens` (D-41). New stages need a price-bearing model
   (`gpt-5.4-nano` for structural/short output, `gpt-5.6-luna` for prose).
3. Store passes are pooled with the entry lock held across read → call → write (D-31);
   copy the pattern in `workflows/retrofit.py` / `workflows/examples.py`, including the
   sentinel note for idempotence and the `stop_event` / `BudgetExceededError` handling.
4. Tests are offline: scripted payloads in `tests/conftest.py` keyed by output type
   (see `_example_hygiene_rewrite_payload` and the registry dict). Every feature adds
   its own `tests/test_<feature>.py`. `uv run ruff check src tests`, `uv run ruff format
   src tests`, `uv run ty check src`, `uv run pytest` must all be clean.
5. Docs: a module docstring explaining *why* (see any workflow module); a `D-nn` entry
   appended to `docs/DECISIONS.md` (numbers are claimed in this plan below to avoid
   collisions); a row in the README "What it does" table; a section in
   `docs/RETRIEVAL-DATA.md` (created by the first agent to need it — append, do not
   rewrite) describing the output format with one real record.
6. Never run a model against `data/core-store` (a live production store with a chain
   running on it). Use `--dry-run`, the offline tests, or the 300-entry copy at
   `data/sample-300` (same sharded layout; safe to write to).
7. Only ledger-measured costs may be quoted. A feature that spends money ships with a
   `--dry-run` plan that prices the run from `expected_output_tokens`, and its D-entry
   records the measured cost of the sample-300 smoke run, nothing projected.
8. No HuggingFace datasets as input. Output files are plain JSONL/TSV under a path the
   caller names.

## Shared schema additions (implemented first, on branch `retrieval/schema`, by the design agent)

```
class Query(_Base):            # F2: a synthetic query that should retrieve this sense
    text: str                  # ≤ 200 chars
    style: QueryStyle          # keyword | question | conversational | constraint |
                               # role | example_based | step_by_step | directive
    provenance_id: str | None

class QAPair(_Base):           # F6
    question: str
    answer: str                # grounded in the sense's gloss / examples / encyclopedia
    question_type: QuestionType  # factual | definition | reasoning | comparison |
                                 # procedural | causal | hypothetical
    difficulty: Difficulty       # easy | medium | hard
    grounded_in: list[str]       # rendition ids the answer is supported by
    provenance_id: str | None

class Contrast(_Base):         # F5: an "X vs Y" discriminating paragraph on one edge
    edge_id: str               # the relation's derived id (e.g. abseil:verb:0-synonym->rappel)
    target_sense_id: str | None
    text: Renditions[str]      # neutral/plain required; more levels optional
    verdict: ContrastVerdict   # related_as_typed | related_differently | unrelated
    provenance_id: str | None

Sense.queries:   list[Query]   = []
Sense.qa:        list[QAPair]  = []
Lexeme.contrasts: list[Contrast] = []
```

All three default empty so every existing entry still validates and `migrate.py` needs
no change. Ids: queries and qa pairs are positional (`<sense_id>#q3`, `<sense_id>#qa3`),
contrasts keyed by `edge_id`. Uniqueness: no two queries with equal normalised text on
one sense; no two QA pairs with equal question; one contrast per edge.

## Features

| # | Feature | Kind | Model | Agent |
|---|---|---|---|---|
| F1 | `export-pairs`: WiC pairs and positive pairs from sense-tagged examples | free | — | Sonnet |
| F2 | `queries` stage: doc2query, 8 styles, ~12 queries per sense | LLM | nano (pilot both) | Opus |
| F3+F4 | `export-triples` (MS MARCO triples with graph hard negatives) and `export-qrels` (graded relevance, TREC qrels + listwise JSONL) | free | — | Sonnet |
| F5 | `contrasts` stage: "X vs Y" paragraph per synonym / antonym / confusable edge, with a verdict | LLM | luna | Opus |
| F6 | `qa` stage: 7 question types × 3 difficulties per sense, answers grounded in stored text | LLM | luna | Opus |
| F7 | register renditions: lexical-diversity target in the prompt + free near-copy check | small | (existing) | Sonnet |
| F8 | `qc filler`: corpus-level n-gram / sentence-starter filler detector, flags renditions | free | — | Sonnet |
| F9 | `export-pretrain`: serialise entries into natural pretraining documents (dictionary, thesaurus, encyclopedia, usage-note templates) | free | — | Sonnet |

Decision numbers reserved: F1 D-54, F2 D-55, F3/F4 D-56, F5 D-57, F6 D-58, F7 D-59,
F8 D-60, F9 D-61. Schema additions D-62.

### F1 — export-pairs (free)

From every entry: for each live sense with ≥2 example renditions, all pairs of its
examples are `same_sense=1`; pairs across different live senses of the same headword are
`same_sense=0` (the hard case, WiC-style); optionally pairs across different headwords in
the same domain leaf as easy negatives (`--easy-negatives N`). Also emit
`(example → gloss)` and `(example → encyclopedia neutral)` positive pairs. Output JSONL
with `headword, sense_a, sense_b, text_a, text_b, span_a, span_b, label, level_a,
level_b`. CLI: `opengloss export-pairs --store S --out pairs.jsonl [--from-list L]`.
Report counts by label. Deterministic; a `--seed` governs any sampling.

### F2 — queries stage (LLM)

One call per sense: instructions describe the 8 styles (copied intent from the other
project's drafting-instruction generator: the query a user would type that this sense's
gloss/encyclopedia answers), the prompt carries headword, POS, canonical gloss, one
example, the domain, and *the other senses' glosses* so queries discriminate senses.
Output: 12 queries, ≥1 per style, ≤ 200 chars, none containing the headword verbatim in
at least half of them (so retrieval isn't lexical). D-47 marker keyed on the sense's
gloss digest. Pilot nano and luna on sample-300 with `--budget 0.50` each; record both
measured costs and a 20-query eyeball in the D-entry; default to the cheaper one that
passes. CLI: `opengloss queries --store S [--from-list L] --budget B --concurrency C
[--dry-run]`.

### F3 + F4 — export-triples and export-qrels (free)

Queries come from F2 when present; if absent, fall back to the sense's `grade_5/plain`
gloss as a pseudo-query (say which in the record). Positives: canonical gloss, one example,
encyclopedia neutral. Hard negatives, in priority order: other live senses of the same
headword; `confusable_with` targets; co-hyponyms (share a hypernym); synonym-of-synonym at
distance 2. Easy negatives: random senses. Triples JSONL: `query, positive, negative,
negative_kind, query_id, positive_id, negative_id`. Qrels: grade 3 = own sense text, 2 =
synonym target's gloss, 1 = hypernym / co-hyponym gloss, 0 = unrelated; TREC format
`qid 0 docid grade` plus a `docs.jsonl` id → text, plus a listwise JSONL
`{query, candidates:[{id,text,grade}]}`. Report grade histograms.

### F5 — contrasts stage (LLM)

For each `synonym`, `antonym`, `confusable_with` edge from a live sense (deduplicated
across the reciprocal), one luna call given both senses' glosses and one example each:
write a 60–120-word paragraph that says how the two differ (or, for antonyms, along what
axis they oppose), and return a `verdict`. `related_differently` / `unrelated` verdicts
are *recorded, not acted on* (relation-hygiene owns edits; see D-50). Marker keyed on
the pair of gloss digests. `--only-kinds` to restrict. This is both a token generator and
a hard-negative explainer.

### F6 — qa stage (LLM)

Per live sense, one luna call producing 7 pairs (one per question type) at mixed
difficulty, answers 1–3 sentences and grounded only in the stored gloss / examples /
encyclopedia (pass those in; instruct: no outside facts; return `grounded_in` rendition
ids). Free post-check: answer must share ≥ 2 content words with the cited renditions,
else drop the pair and count it. Marker keyed on the gloss digest.

### F7 — register diversity (small)

Add to the register-rendition instructions a target lexical diversity of 0.30–0.60
versus the canonical gloss (defined as 1 − Jaccard over content-word sets) and a ban on
copying the canonical verbatim. Free post-check in `enrich.py` (or wherever renditions are
accepted): a register rendition with Jaccard ≥ 0.9 against the canonical is rejected and
regenerated once, then flagged `OG_NEAR_COPY` (new QAFlag). Add the same check to
`retrofit.rendition_hygiene`. Measure the current near-copy rate on sample-300 first
and record it.

### F8 — qc filler (free)

Across all example renditions (and encyclopedia text), count 4-gram and sentence-opener
frequencies; anything above a frequency threshold (e.g. a 4-gram appearing in > 0.05% of
sentences, or an opener like "The researchers" in > 0.5%) is listed with examples. Also a
per-entry uniqueness score (type/token ratio) and information-density score copied in
intent from the other project's `estimate_document_quality`. Output: a report JSON and,
with `--flag`, `OG_FILLER` set on offending renditions' assessments so a later rewrite
pass can target them. CLI: `opengloss qc filler --store S --out report.json [--flag]`.

### F9 — export-pretrain (free)

Serialise each entry into 3–4 natural document templates (choose per entry with a seed so
the corpus mixes them): a dictionary entry (headword, POS, numbered senses, examples), a
thesaurus entry (senses with synonyms / antonyms / see-also), an encyclopedia article
(encyclopedia renditions, etymology, lexical explanation), and a usage note (contrasts
from F5 when present, register variants side by side). Plain prose/markdown, no JSON /
YAML duplicates, no special tokens. One document per line in JSONL `{id, template,
text, n_words}`, reading level selectable (`--levels grade_5,college`). Report words per
template. This is also the missing HF-export precursor named in the README.

## Working rules for agents

* Work in your own git worktree on a branch named `retrieval/<feature>`; base it on
  `retrieval/schema` once that branch exists (F2, F5, F6, F9 need the new fields; F1,
  F3/F4, F7, F8 may base on `main`).
* You own: your workflow / export module, your test file, your docs section, your
  D-entry. Shared files you may touch with *minimal, additive* edits only: `cli.py` (one
  command registration), `config.py` (one policy), `schema.py` (`StageName` / `QAFlag`
  members only), `tests/conftest.py` (one payload function + one registry line),
  `README.md` (one table row), `docs/DECISIONS.md` (append your reserved number).
* Do not refactor neighbours. Do not touch `data/core-store` or `runs/`.
* Finish with: all four checks green, the smoke run on `data/sample-300` (dry-run for
  paid stages unless the plan above says to pilot), and a final report listing files
  changed, tests added, measured numbers, and anything you left undone.
