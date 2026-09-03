# OpenGloss Generator — Requirements

Status: **v0.1 draft**
Author: Michael Bommarito
Date: 2026-09-02

## 1. Purpose

`opengloss-generator` is a clean-room reimplementation of the generation and enrichment
pipeline that produced [OpenGloss](https://arxiv.org/abs/2511.18622) v1.0–v1.3.

The v1.0–v1.3 pipeline lived inside the `curriculum` project (an educational-content
generator that grew a lexicon subsystem). That coupling produced three concrete problems
this project exists to fix:

| Problem in the v1.3 pipeline | Requirement here |
|---|---|
| Node identity was random UUIDs minted at generation time, and the HuggingFace export dropped them. The published datasets therefore cannot be joined back to the working store, and a rebuild from HF would renumber every sense and edge. | **Deterministic, derivable identifiers** (FR-5). The export round-trips. |
| Cost control was ad hoc; the total spend across all iterations was only known retrospectively. | **Per-call cost accounting and a hard budget ceiling** (FR-6). |
| A definition was a single string. Reading level and register were properties of the *whole dataset*, not of the definition, so a second reading level meant a second dataset. | **Definitions are a set of variants** keyed by (reading level, register) (FR-3, FR-9). |

Everything else — the multi-agent decomposition, schema-validated outputs, snowball
expansion through the semantic graph — is retained, because it worked.

## 2. Scope

### In scope

- Generating a new lexeme entry from a specification.
- Expanding the graph by walking it and generating entries for dangling relation targets.
- Enriching an existing entry with additional fields or additional definition variants.
- Cost accounting, budgeting, and cost-minimizing execution modes.
- Local content store with safe concurrent access.
- CLI for all of the above.

### Out of scope (v0.1)

- Publishing to HuggingFace (a separate export step; the store is designed to make it
  mechanical, but shipping it is not part of this milestone).
- Multilingual generation. The schema carries a `language` field and does not assume
  English, but only English is exercised.
- Embedding/model training (that is `opengloss-embedding`'s job).
- Serving/query APIs.
- Migrating the existing 205,996-entry v1.3 store. An importer is a follow-on;
  the schema is designed to accept it (see `DESIGN.md` § Migration).

## 3. Functional requirements

### FR-1 — Generate an entry from a specification

Given a headword and an optional `EntrySpec` (target parts of speech, sense count bounds,
domain hint, reading levels, registers, which optional sections to fill), produce a
complete, schema-valid `Lexeme`.

- **FR-1.1** The caller may under-specify. Everything except the headword has a default.
- **FR-1.2** Generation is staged: an *overview* stage decides POS inventory and sense
  counts, then a *sense* stage generates senses per POS. Stages are independently
  retryable and independently cacheable.
- **FR-1.3** Every stage output is validated against a Pydantic model before it is
  accepted. Invalid output is retried with the validation error fed back; after
  `max_attempts` the stage fails and the entry is written as `partial`, never as invalid.
- **FR-1.4** A generated entry records its provenance: model id, prompt version, service
  tier, token usage, cost, and UTC timestamp — per stage, not just per entry.

### FR-2 — Grow the graph by walking it

- **FR-2.1** Sample a node from the store under a configurable strategy
  (`random`, `least-connected`, `highest-frequency`, `explicit`).
- **FR-2.2** Collect that node's outbound relation targets (synonyms, antonyms,
  hypernyms, hyponyms, derivations, collocation heads) and determine which targets have
  no entry in the store — the *frontier*.
- **FR-2.3** Filter the frontier before spending money on it: drop non-headwords
  (etymology roots, glosses that leaked into a relation slot, multi-word artifacts that
  are not lexical units), respecting a configurable filter chain. Cheap deterministic
  filters run before any LLM classifier.
- **FR-2.4** Optionally ask the model for additional related terms not already present,
  to widen the frontier beyond what the source entry literally names.
- **FR-2.5** Generate entries for surviving frontier terms via FR-1, and record the
  provenance edge "discovered from *X* during walk *R*".
- **FR-2.6** A walk is bounded by *all* of: max new entries, max budget, max wall-clock,
  and max depth. Whichever binds first stops the walk cleanly.
- **FR-2.7** A walk never generates the same headword twice within a run, and never
  regenerates an existing entry unless `--force`.

### FR-3 — Enrich an existing entry

- **FR-3.1** Add missing top-level sections (etymology, encyclopedia entry, lexical
  explanation) without touching sections that already exist.
- **FR-3.2** Add definition variants at requested reading levels
  (`grade_1`, `grade_5`, `grade_10`, `college`) for senses that lack them.
- **FR-3.3** Add definition variants in requested registers
  (`plain`, `informal`, `technical`, `formal`, `marketing`) for senses that lack them.
- **FR-3.4** Variants for one sense are generated in a single call that sees the canonical
  gloss and all sibling variants, so the set is internally consistent and mutually
  non-redundant. Generating them one-per-call is explicitly rejected: it costs more and
  produces variants that drift.
- **FR-3.5** Enrichment is idempotent. Re-running with the same targets is a no-op that
  costs nothing.
- **FR-3.6** Improving (replacing) an existing field is possible but must be explicit
  (`--replace`), and the superseded value is retained in the entry's revision history.

### FR-4 — Concurrency

- **FR-4.1** Work is executed by a bounded pool of async workers.
- **FR-4.2** Requests are governed by a per-provider rate limiter covering both requests
  per minute and tokens per minute.
- **FR-4.3** Two workers never write the same entry concurrently, in-process or
  cross-process.
- **FR-4.4** A crash, `SIGINT`, or budget stop leaves the store in a consistent state:
  every entry file on disk is either absent or complete and valid. No partial files.
- **FR-4.5** A run is resumable. Re-running the same job skips completed work.

### FR-5 — Identity and storage

- **FR-5.1** Identifiers are **deterministic functions of position in the structure**,
  not random. `lexeme_id = slug(headword)`; `sense_id = "{lexeme_id}:{pos}:{index}"`;
  `variant_id = "{sense_id}#{reading_level}/{register}"`; an edge's id is derived from
  its endpoints and type. Any consumer can recompute an id from an export.
- **FR-5.2** One JSON file per lexeme, sharded into subdirectories so no directory
  exceeds ~4096 entries. (The v1.3 store put 205,996 files in a single directory; that
  is why `du` on it takes ten minutes over NFS.)
- **FR-5.3** Writes are atomic (temp file in the same directory, then `os.replace`).
- **FR-5.4** Every run appends to a JSONL ledger recording each unit of work, its
  outcome, its usage, and its cost.
- **FR-5.5** Reads tolerate a store being concurrently written.

### FR-6 — Cost

- **FR-6.1** Every model call's cost is computed from its actual reported usage and a
  versioned price table, and attributed to the entry and stage that incurred it.
- **FR-6.2** A run declares a budget. When projected spend would exceed it, no further
  work is dispatched; in-flight work completes and the run stops cleanly.
- **FR-6.3** `--dry-run` estimates the cost of a job without making calls.
- **FR-6.4** Cost-saving defaults are on unless overridden:
  - OpenAI `service_tier="flex"` (Batch-equivalent rates on the synchronous API);
  - prompt layout ordered for cache hits (static system prompt first, volatile input last);
  - the batch path for jobs above a size threshold;
  - the cheapest model that meets the stage's quality bar, per stage, not per run.
- **FR-6.5** The price table is data, carries an `as_of` date and a source URL, and is
  unit-tested against the model ids the config can select.

### FR-7 — Configuration

- **FR-7.1** Layered, in increasing precedence: built-in defaults → config file →
  environment (`OPENGLOSS_*`) → CLI flags.
- **FR-7.2** Configuration is a validated Pydantic model. An invalid config fails at
  startup with a readable error, before any spend.
- **FR-7.3** Secrets are read from the environment only and are never logged or persisted.

### FR-8 — Observability

- **FR-8.1** Structured logging (JSONL to file, human-readable to console), with a
  `run_id` on every event.
- **FR-8.2** Every model call logs model, stage, attempt, latency, usage, cost, and
  outcome. Prompt and completion bodies are logged only at `TRACE`.
- **FR-8.3** A run ends with a summary: entries attempted/succeeded/failed, tokens by
  model, cost by model and by stage, wall-clock, and cache-hit rate.

### FR-9 — Data model

- **FR-9.1** A `Sense` has one canonical `gloss` plus zero or more `DefinitionVariant`s,
  each tagged with reading level and register. The canonical gloss is itself addressable
  as the `(neutral, plain)` variant.
- **FR-9.2** Relations are stored on the sense that asserts them and are *also* derivable
  as a flat edge list. The edge list is a projection, not a second source of truth.
- **FR-9.3** Unknown fields are rejected (`extra="forbid"`), so a schema drift is a test
  failure and not a silent data loss.
- **FR-9.4** Every entry carries a `schema_version`.

### FR-10 — CLI

`opengloss generate`, `opengloss walk`, `opengloss enrich`, `opengloss show`,
`opengloss stats`, `opengloss price`. Every command that spends money supports
`--dry-run`, `--budget`, and `--concurrency`.

### FR-11 — Typed relations and sense resolution

- **FR-11.1** A sense's relations are one typed list. Each relation has a `type` (one of
  14 `RelationType` values), a `target`, an optional `note`, and an optional
  `provenance_id`. A new relation type is an enum value, not a schema change.
- **FR-11.2** A relation target begins as an unresolved surface form (`term`,
  `sense_id=None`, `confidence=None`). Its lexeme id (`slugify(term)`) is derived, never
  stored, and a term that cannot yield a slug is rejected at validation time.
- **FR-11.3** A `confusable_with` relation requires a non-empty `note` explaining how the
  two terms differ; the note is the content of that relation type, not decoration.
- **FR-11.4** A `resolve` stage fills `sense_id` and `confidence` on targets whose
  lexeme exists in the store; a target whose lexeme does not exist stays unresolved at
  zero cost. Resolving a target never changes the edge id derived from it, so ids are
  stable across resolution.
- **FR-11.5** The flat edge list remains a derived projection (`Lexeme.edges()`), never a
  second source of truth, and now carries `target_sense` and `confidence`; retired
  senses are excluded from it.

### FR-12 — Kind discriminator

- **FR-12.1** Every lexeme carries a `kind` (`LexemeKind`: `simplex`, `compound`,
  `phrasal_verb`, `idiom`, `proper_noun`, `abbreviation`, `affix`, `function_word`).
  Sampling, QA, prompt selection, and multi-word structure branch on it.
- **FR-12.2** A deterministic rule set classifies a headword before any model call:
  leading/trailing hyphen → affix; alphabetic all-caps of at most 3 letters →
  abbreviation; closed-class membership → function word; leading capital → proper noun;
  internal hyphen → compound; whitespace is ambiguous and deferred to a batched
  classifier; everything else is simplex.
- **FR-12.3** `kind == proper_noun` if and only if a `proper_noun` block (`entity_type`,
  optional `wikidata_qid`) is present. `kind == function_word` forces `is_stopword`.
- **FR-12.4** The `classify_kind` stage measures and logs the fraction of headwords
  resolved deterministically versus by the model, against an expectation of more than
  85% resolved without a call.

### FR-13 — Domain taxonomy and deficit sampling

- **FR-13.1** A sense's domain is a controlled `DomainTag` drawn from a fixed taxonomy of
  15 roots and roughly 150 leaves (each root also has a `.general` catch-all), not free
  text.
- **FR-13.2** The full leaf listing is available as a byte-stable prompt block for
  inclusion in cached instructions, so a domain-tagging call pays for it once per cache
  lifetime rather than once per call.
- **FR-13.3** A legacy free-text domain resolves through a fixed legacy-to-taxonomy map
  or, failing that, an exact match to a taxonomy root's own name, onto that root's
  `.general` leaf. Anything else is kept as a hint for a later retagging pass rather than
  guessed at.
- **FR-13.4** The graph-growing workflow can select seeds by domain deficit: each root's
  target share of a uniform `1/15` minus its actual observed share in the store, so
  under-represented domains are sampled first.

### FR-14 — Structured examples with spans

- **FR-14.1** An example is text plus an optional half-open character span identifying
  the headword occurrence. Validation requires `0 <= start < end <= len(text)` when a
  span is present.
- **FR-14.2** Spans are found deterministically first: case-insensitive whole-word match
  of the headword, then each inflected form, then a hyphen/space/underscore-insensitive
  multi-word match. The longest match wins; ties resolve to the earliest occurrence.
- **FR-14.3** Only examples the deterministic finder cannot place go to an LLM fallback,
  batched. Irregular inflections, hyphen-flanked affix headwords, and text where
  casefolding would change length are deliberately left to that fallback rather than
  handled by a heuristic that could silently mismatch offsets. Deterministic placement
  is expected to exceed 90% of examples.

### FR-15 — Uniform rendition enrichment

- **FR-15.1** Every text-bearing field — a sense's gloss, a sense's examples, an entry's
  encyclopedia entry, an entry's lexical explanation — is a set of renditions keyed by
  `(reading level, register)`, not a bare string plus a side list.
- **FR-15.2** The canonical `(neutral, plain)` rendition is required for a sense's gloss
  and is the text every other rendition of that field is generated from.
- **FR-15.3** Enrichment computes the targets a field is missing against what it already
  has and produces exactly those in one call per (owner, field); a field with no gaps
  costs nothing (this generalizes FR-3.5 to every rendition-bearing field, not just
  definitions).
- **FR-15.4** Encyclopedia renditions default to reading-level variation only, at the
  `plain` register, because their canonical text is long enough that a full
  level-by-register cross product would dominate the cost of an enrichment sweep (see
  `COST-MODEL.md`).

### FR-16 — Idempotent retrofit passes

- **FR-16.1** A retrofit workflow runs one or more of `classify_kind`, `tag_domain`, and
  `spans` over an existing store, individually selectable.
- **FR-16.2** Each pass is idempotent: an entry or field that is already filled is
  skipped, so re-running retrofit over an unchanged store makes no calls and costs
  nothing.
- **FR-16.3** Migration never leaves `kind` unset so that retrofit always has a
  well-defined starting point rather than a null to special-case: an ambiguous
  multi-word headword migrates as `compound` (D-12), and a migrated proper noun's
  `entity_type` defaults to `other` (D-12). Both are documented placeholders, not
  classifications.
- **FR-16.4** The `classify_kind` pass logs the same deterministic-versus-model
  resolution ratio required by FR-12.4, so retrofit and fresh generation share one
  measurement of how well the deterministic rules are holding up.

## 4. Non-functional requirements

- **NFR-1** Python ≥ 3.14. `uv` for environment and locking; `ruff` for lint and format;
  `ty` for type checking. All three clean in CI.
- **NFR-2** The full test suite runs offline with no API key and no network, using
  pydantic-ai's `TestModel`/`FunctionModel`. Tests that require a provider are marked
  `live` and deselected by default.
- **NFR-3** Public functions and all module/class definitions carry docstrings and
  complete type annotations (enforced by ruff `D`/`ANN`).
- **NFR-4** No global mutable state. Every component takes its dependencies explicitly,
  so a test can substitute them.
- **NFR-5** The store format is plain JSON, readable without this package.

## 5. Acceptance criteria

### v0.1 acceptance criteria — met

The milestone is met when, offline and with a fake model:

1. `opengloss generate --headword abseil` writes a schema-valid entry with provenance
   and a nonzero recorded cost.
2. `opengloss walk --seed abseil --max-new 5` discovers dangling relation targets, filters
   them, generates entries, and stops on whichever bound binds first.
3. `opengloss enrich --headword abseil --reading-levels grade_1,grade_5,grade_10,college`
   adds exactly the missing variants, and a second run is a no-op costing $0.
4. `opengloss enrich --headword abseil --registers informal,technical,formal,marketing`
   likewise.
5. A run interrupted mid-flight leaves no invalid file in the store, and re-running
   completes the remaining work.
6. `ruff check`, `ruff format --check`, `ty check`, and `pytest` all pass.

`README.md` records these six as passing offline as of the v0.1 status line; schema v3
(FR-11–FR-16) is layered on top of that baseline, not a replacement for it — criteria
3 and 4 above now exercise the `Renditions[T]` machinery described in `DESIGN.md` §2.2
rather than the old `DefinitionVariant` list, with no change to the CLI surface.

### v3 acceptance criteria

The v3 milestone (FR-11–FR-16) is met when, offline and with a fake model:

7. `migrate.from_v2` and `migrate.from_v13` each upgrade a representative legacy payload
   to a schema-valid v3 `Lexeme`, preserving source sense order and never renumbering a
   sense.
8. `classify_kind_deterministic` resolves every documented rule-set case without a model
   call, and the `classify_kind` retrofit pass records the deterministic-resolution
   ratio it achieved on a mixed input.
9. `tag_domain` assigns a `DomainTag` from the fixed taxonomy to every sense of an entry;
   a legacy free-text domain resolves through the legacy map or a root-name match, and
   anything else lands in `domain_hint` rather than a guess.
10. `find_span` locates the documented deterministic cases (headword and inflected
    forms, hyphen/space/underscore-flexible multi-word terms) and leaves irregular
    inflections, affix headwords, and casefold-length-changing text for the batched LLM
    fallback.
11. `Renditions[T]` rejects a duplicate `(reading_level, register)` key (and, for
    examples, a duplicate `(reading_level, register, text)` key), and a `Sense` fails
    validation without a canonical `(neutral, plain)` gloss.
12. `opengloss resolve` and `opengloss retrofit` (planned; not yet in `cli.py` — see
    `README.md`) run over a store and are idempotent: a second run over an unchanged
    store makes no model calls.

## 6. Open questions

- **OQ-1** Does the OpenAI Batch API path justify its complexity for walks, given flex is
  already at batch rates? Provisional answer: yes for enrichment sweeps over ≥10⁴ entries
  (higher rate ceilings), no for interactive walks. Revisit with measurements.
- **OQ-2** Should the frontier classifier be an LLM at all, or does a dictionary-membership
  + morphology heuristic get close enough for a fraction of the cost? v1.3 used
  `gpt-5.4-mini` over ~2.1M candidates. Measure the heuristic's agreement rate first.
- **OQ-3 (updated for v3)** Reading-level control: prompt-only, or validated post-hoc
  with a readability metric and regenerated on failure? The schema question this used to
  gate is resolved: the per-variant `measured_grade_level` field is gone, and
  `readability_grade` now lives on the general-purpose `Assessment` slot (`DESIGN.md`
  §2.6), attachable to a `Rendition`, a `Sense`, or the whole `Lexeme`. What remains open
  is unchanged in substance — prompt-only for v0.1/v3; nothing yet populates
  `readability_grade` from a measured metric or regenerates a rendition on failure.
