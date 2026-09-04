# opengloss-generator

Schema-validated, cost-aware LLM generation and enrichment for the
[OpenGloss](https://arxiv.org/abs/2511.18622) lexical knowledge graph.

A clean-room reimplementation of the pipeline that produced OpenGloss v1.0–v1.3, built
around three things that pipeline lacked: **derivable identifiers** (so exports
round-trip), **per-call cost accounting with a hard budget**, and every text field —
definitions, examples, the encyclopedia entry — as a set of reading-level × register
**renditions** rather than a single string.

Schema v3 (current) adds four structural pieces on top of that: a `kind` discriminator
on every lexeme (simplex, compound, phrasal verb, idiom, proper noun, abbreviation,
affix, function word), a controlled ~150-leaf domain taxonomy per sense in place of
free-text domains, one typed `relations` list per sense whose targets *resolve* to
sense ids instead of staying bare strings, and structured examples carrying the
character span of the headword occurrence. See `docs/SCHEMA-V3.md` for the contract and
`docs/DESIGN.md` § 2 for how it fits together.

- Requirements: [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md)
- Design: [`docs/DESIGN.md`](docs/DESIGN.md)
- Schema v3 contract: [`docs/SCHEMA-V3.md`](docs/SCHEMA-V3.md)
- Cost model (per-stage arithmetic, worked estimates): [`docs/COST-MODEL.md`](docs/COST-MODEL.md)
- Research log (verified versions, prices, API facts): [`docs/RESEARCH.md`](docs/RESEARCH.md)
- Decision log: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Quick start

```bash
uv sync --all-extras                 # Python ≥ 3.14, pydantic-ai 2.37
export OPENAI_API_KEY=...            # generation
export ANTHROPIC_API_KEY=...         # QA/judge stage only

uv run opengloss price               # what each stage will cost, per 1M tokens
uv run opengloss generate --headword abseil --budget 0.05
uv run opengloss show --headword abseil
uv run opengloss show --headword abseil --edges

# grade the definitions for four audiences, one call per sense
uv run opengloss enrich --headword abseil --reading-levels grade_1,grade_5,grade_10,college
# add parallel registers
uv run opengloss enrich --headword abseil --registers informal,technical,formal,marketing
# a second run of either is a no-op costing $0

# grow the graph outward from an entry, bounded by count, depth, budget, and time
uv run opengloss walk --seed abseil --max-new 20 --max-depth 2 --budget 1.00

uv run opengloss stats

uv run opengloss resolve --headword abseil            # fill in relation target sense ids
uv run opengloss retrofit --only classify_kind        # bring a migrated store to v3 parity
uv run opengloss migrate --from /path/to/v13/store    # v1.3 or v2.0 payloads -> schema v3
```

Every command that spends money accepts `--budget`, `--concurrency`, and `--dry-run`, and
prints a JSON run summary with actual cost, tokens, cache-hit rate, and cost by stage.
Structured logs and an append-only ledger land in `runs/<run_id>.*`.

## What it does

| Workflow | Command | What happens |
|---|---|---|
| Generate from spec | `generate` | overview (incl. `kind`, per-sense `domain`) → per-POS senses (relations, confusables, examples; concurrent) → etymology / encyclopedia / usage note (concurrent). Every stage is schema-validated and retried with the validation error fed back. |
| Walk the graph | `walk` | Sample a node, harvest its dangling relation targets, filter them (free filters first, LLM triage last), generate the survivors, repeat to depth. Stops on the first of max-entries / budget / deadline / depth. |
| Enrich | `enrich` | Diff what an entry has against what was asked for; generate only the missing renditions. Reading levels and registers are crossed; all renditions for one (sense, field) come from one call. |
| Resolve | `resolve` | Fill in `sense_id` / `confidence` on relation targets whose lexeme already exists in the store; targets absent from the store stay unresolved at zero cost. |
| Retrofit | `retrofit` | Run `classify_kind`, `tag_domain`, and/or `spans` over an existing store; idempotent, `--only` selects one pass. |
| QA pairs | `qa-pairs` | One call per live sense buys seven question/answer pairs — one of each question type, at mixed difficulty — answered only from that sense's own stored text (gloss, examples, encyclopedia, etymology), each source labelled with an id the answer must cite. Uncited, mis-cited, ungrounded and duplicate pairs are dropped and counted. Not `qa`, which is the Opus judge. |
| Migrate | `migrate` | Upgrade a v1.3 or v2.0 payload to schema v3 via `migrate.from_v13` / `migrate.from_v2`; never renumbers a sense. |
| Export retrieval pairs | `export-pairs` | Free: mine WiC-style positive/hard-negative pairs from a sense's own example renditions, plus example→gloss and example→encyclopedia positives; `--easy-negatives N` adds sampled cross-headword, same-domain negatives. See `docs/RETRIEVAL-DATA.md`. |
| Filler QC | `qc filler` | Count 4-grams and sentence openers across the renditions `--fields` selects (default `examples`; `encyclopedia`/`all` also count encyclopedia text); report what recurs more than chance, with a per-entry uniqueness / information-density score. No model call. `--flag` sets `OG_FILLER` on the offenders; `--unflag` reverses it (D-60, D-66). |
| Filler QC calibration | `qc filler-calibrate` | Read-only: measure the `qc filler` flag rate at several threshold combinations in one pass, with each point's top findings by sentence count. Free, no model call. Used to pick `qc filler`'s own defaults against a production store (D-66). |
| Export triples | `export-triples` | MS MARCO-style `(query, positive, negative)` JSONL, one hard negative per query drawn from the resolved graph (same-headword sense, `confusable_with`, co-hyponym, synonym-of-synonym) plus configurable easy negatives. Free; deterministic for a given `--seed`. See `docs/RETRIEVAL-DATA.md`. |
| Export qrels | `export-qrels` | TREC-style graded qrels (`qrels.trec`), a `docs.jsonl` corpus, and a listwise JSONL, graded 3 (own sense) down to 0 (unrelated) from the same graph. Free; deterministic. See `docs/RETRIEVAL-DATA.md`. |
| Export pretraining docs | `export-pretrain` | Serialise each entry into up to four plain-prose documents (dictionary, thesaurus, encyclopedia, usage note) as JSONL; `--levels`/`--templates`/`--per-entry`/`--seed` select what to render and how the corpus mixes. Free; makes no model calls (see `docs/RETRIEVAL-DATA.md`). |
| Contrast | `contrasts` | One "X vs Y" paragraph per synonym / antonym / confusable edge, saying how the two terms actually differ, plus a verdict on whether they are related the way the edge claims. One call per entry covers up to eight pairs; a pair whose two ends are both in the store is written once, on the smaller end; verdicts are recorded, never acted on (D-50). |
| Queries (doc2query) | `queries` | One call per live sense writes N synthetic search queries across the eight `QueryStyle` registers, with the entry's other senses in the prompt so the queries discriminate between them and at least half of them asked to describe the meaning without naming the headword. Duplicates and over-long queries are dropped for free; the achieved headword-free share is reported. Idempotent per sense (D-55). |
| Reconcile relations | `relation-reconcile` | Free: demote every edge a stored `contrasts` verdict says is not what it claims (both sides of a symmetric pair), apply the stricter of two disagreeing directional verdicts on a symmetric pair, take every demoted `see_also` out of the sense's relation list (recorded to provenance, so nothing is lost), drop exact duplicates and cap each sense's per-type runs. This is what shortens the list the QA judge is shown; reciprocity goes up, not down. Idempotent; `--dry-run` computes every edit and writes nothing (D-65, D-68). |

## Cost defaults

- OpenAI `service_tier="flex"` — Batch-API pricing on the synchronous API (verified
  2026-09-02). Automatic downgrade to `auto` after repeated capacity rejections.
- Static instructions first, volatile input last, per-stage prompt cache key — so the
  provider's prefix cache actually hits.
- Cheapest model that clears each stage's bar: `gpt-5.4-nano` for structural stages,
  `gpt-5.6-luna` for content, `claude-opus-5` for QA (a different family, so QA is not
  marking its own homework).
- Cost is computed from *reported* usage against a versioned price table, with cached
  input priced at the cached rate. A model with no price row cannot be selected.

## Development

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src
uv run pytest                        # offline, no API key needed
uv run pytest -m live                # one real call, capped at $0.10
```

The entire suite runs against a scripted `FunctionModel`; nothing touches the network
unless you opt into `-m live`.

## Store layout

```
data/store/<hh>/<hh>/<lexeme_id>.json     # hash-sharded, atomic writes, lock-per-entry
runs/<run_id>.log.jsonl                    # structured log
runs/<run_id>.ledger.jsonl                 # one record per unit of work, with cost
```

Entries are plain JSON, one `Lexeme` (schema v3) per file. A lexeme's `kind`
discriminator, per-sense `domain` tag, typed `relations`, and every text field's
`Renditions[T]` set are stored inline — nothing v3 added lives in a side table.
Identifiers are positional and derivable by any consumer from the entry alone:
`abseil:verb:0` (sense), `abseil:verb:0#grade_5/plain` (rendition),
`abseil:verb:0-synonym->rappel` (edge, keyed by the *target's slug*, so resolving a
target's `sense_id` never changes the edge's id).

## Status

v0.1 — schema v3 (`docs/SCHEMA-V3.md`) is implemented end to end: `schema.py`,
`identity.py`, `migrate.py`, `taxonomy.py`, `spans.py`, `contracts.py`, `prompts.py`,
`config.py`, `stages.py`, and every workflow (`generate`, `walk`, `enrich`, `resolve`,
`retrofit`) consume the v3 contract, with deviations from the contract text recorded in
`docs/DECISIONS.md` (D-10 onward) and cross-indexed from `docs/SCHEMA-V3.md` § 8. All
acceptance criteria in `docs/REQUIREMENTS.md` § 5 pass offline. Not yet included: the
HuggingFace export step and the Batch-API submission path (the config threshold exists;
the submitter does not).
