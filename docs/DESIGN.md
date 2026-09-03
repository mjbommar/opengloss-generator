# OpenGloss Generator — Design

Companion to `REQUIREMENTS.md`. Date: 2026-09-02. Facts about library and provider
behaviour cited here are established in `RESEARCH.md`.

## 1. Shape of the system

```
                 ┌──────────────┐
   CLI ────────► │  Job spec    │   what to do, how much to spend
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │  Runner      │   TaskGroup + bounded workers + budget guard
                 └──┬────────┬──┘
                    │        │
     ┌──────────────▼──┐  ┌──▼──────────────┐
     │  Workflow       │  │  Ledger writer  │  single writer, append-only JSONL
     │  (generate /    │  └─────────────────┘
     │   walk /        │
     │   enrich)       │
     └────────┬────────┘
              ▼
     ┌─────────────────┐    ┌──────────────┐   ┌──────────────┐
     │  Stage          │───►│  ModelRouter │──►│  RateLimiter │
     │  (pydantic-ai   │    │  per-stage   │   │  RPM + TPM   │
     │   Agent)        │    │  model+tier  │   └──────────────┘
     └────────┬────────┘    └──────┬───────┘
              │                    ▼
              │             ┌──────────────┐
              │             │  CostMeter   │  price table × reported usage
              │             └──────────────┘
              ▼
     ┌─────────────────┐
     │  LexemeStore    │   sharded, atomic, lock-per-entry
     └─────────────────┘
```

Layers only depend downward. `store` knows nothing about models; `models` knows nothing
about workflows. Every component takes its collaborators as constructor arguments
(NFR-4), so tests substitute a `TestModel` and a `tmp_path` store with no patching.

## 2. Data model

### 2.1 Identity — the central decision

v1.3's identifiers were `uuid4()` minted at generation time and dropped by the export.
Here **every identifier is a pure function of the entry's structure**:

| Entity | Identifier | Example |
|---|---|---|
| Lexeme | `slug(headword)` | `abseil` |
| POS entry | `{lexeme_id}:{pos}` | `abseil:verb` |
| Sense | `{lexeme_id}:{pos}:{index}` | `abseil:verb:0` |
| Rendition | `{owner_id}#{reading_level}/{register}` | `abseil:verb:0#grade_5/plain` |
| Edge | `{source_sense_id}-{relation}->{slug(target_term)}` | `abseil:verb:0-synonym->rappel` |

`owner_id` is a sense id for a gloss or example rendition, and `{lexeme_id}:encyclopedia`
/ `{lexeme_id}:explanation` for the two entry-level prose fields
(`identity.rendition_id`; v2's `variant_id` now just forwards to it, unchanged format).

Consequences:

- An export can omit ids entirely; any consumer recomputes them.
- Re-generating an entry does not renumber anything downstream of it, as long as sense
  order is preserved — so the store preserves sense order across regenerations and
  appends new senses rather than reordering.
- Two independent runs that produce the same structure produce the same ids, which makes
  the store content-comparable across machines.
- `Edge.edge_id` is built from `slugify(target.term)`, never from the sense the
  `resolve` stage later attaches (§5.5). Resolving a relation's target therefore changes
  what the edge points at (`target_sense`, `confidence`) without changing the edge's own
  identity — a projection computed before and after resolution is diff-able.

The cost is that a sense's id is positional, so deleting sense 0 renumbers senses 1..n.
Deletion is therefore a tombstone (`retired: true`), not a removal.

### 2.2 Renditions: one operation for every text field, not just definitions

```
Sense
├── gloss: Renditions[str]            canonical (neutral, plain) required
├── examples: Renditions[Example]     text + optional headword span; several canonical
│                                      examples may coexist
├── relations: list[Relation]         see § 2.4
├── domain, secondary_domains, domain_hint
└── assessment                        see § 2.6
```

`Rendition[T]` is generic over the payload — a gloss string, an `Example`, a prose
section — so `Sense.gloss`, `Sense.examples`, `Lexeme.encyclopedia`, and
`Lexeme.lexical_explanation` are all the same shape: a `Renditions[T]` set keyed by
`(reading_level, register)`. That uniformity is what makes `enrich` (§5.4) one operation
instead of a special case per field (FR-15), and is the schema change that makes
FR-3.2/3.3 a normal enrichment rather than a new dataset.

A `(reading_level, register)` pair is unique within a set — except `examples`, where
several canonical examples may legitimately coexist, so their uniqueness key also
includes the example text (`content.text`). The canonical `(neutral, plain)` rendition
is required on every sense's `gloss`, and is the text every other rendition of that
field is generated from.

Renditions for one field are still generated **as a set in one call** (FR-3.4): one call
that sees the canonical text and produces the missing graded rewrites is cheaper than
one call per target, and the model can differentiate the outputs from each other. Independent
calls converge on the same middle register.

The Python attribute for register is `style`, not `register` (D-5): naming a Pydantic
field `register` collides with `ABCMeta.register`, which every model class carries via
its metaclass, and Pydantic silently treats the bound method as an implicit default. The
wire name stays `register`.

### 2.3 Kind and taxonomy: two discriminators, one on the lexeme and one per sense

`Lexeme.kind` (`LexemeKind`: `simplex`, `compound`, `phrasal_verb`, `idiom`,
`proper_noun`, `abbreviation`, `affix`, `function_word`) is the node's top-level type.
Sampling, QA, prompt selection, and multi-word structure all branch on it, so it is a
discriminator, not a tag: a validator requires the `proper_noun` block (`entity_type`,
optional `wikidata_qid`) exactly when `kind == proper_noun`, and forces `is_stopword`
whenever `kind == function_word`.

Most of `kind` is free: `classify_kind_deterministic` (`migrate.py`) resolves it from
the surface form alone — leading/trailing hyphen → affix, short all-caps → abbreviation,
closed class → function word, leading capital → proper noun, internal hyphen →
compound (D-11) — and only a bare, space-containing residue (genuinely ambiguous
between compound, phrasal verb, and idiom) goes to the batched `classify_kind` model
stage. `docs/SCHEMA-V3.md` §5 expects more than 85% of headwords to resolve without a
call; the stage measures and logs the actual ratio so that expectation is checked, not
assumed.

Domain is per sense, not per entry, and is a controlled `DomainTag` (`taxonomy.py`): 15
fixed roots (`ROOTS`), each with 8-12 leaves and always a `.general` catch-all, ~150
leaves total. The full leaf listing lives in `TAXONOMY_PROMPT_BLOCK`, a byte-stable
string built once at import time so it sits in cached *instructions* rather than being
re-sent, and re-priced, on every `tag_domain` call — §4.3's caching rule applied to a
whole reference block, not just a system prompt. A legacy free-text `domain` string from
v1.x resolves through `LEGACY_DOMAIN_MAP`, or — for the entries tagged with a taxonomy
root's bare name — to that root's `.general` leaf; anything else is kept verbatim in
`domain_hint` for the `tag_domain` retrofit pass, rather than guessed at.

### 2.4 Relations are typed, and targets resolve to senses without moving

```
Sense.relations: list[Relation]
Relation
├── type: RelationType        14 values — synonym, antonym, hyper/hyponym, mer/holonym,
│                              derivation, collocation, confusable_with, see_also,
│                              causes, entails, used_with, instance_of
├── target: RelationTarget
│     ├── term                surface form as produced
│     ├── sense_id            None until resolved
│     └── confidence          None until resolved
├── note                      required when type is confusable_with
└── provenance_id
```

One typed list replaces v2's six parallel lists (`synonyms`, `antonyms`, ...): a new
relation type is an enum value, not a schema change. A target begins life as nothing
more than the surface form the model produced; `RelationTarget.lexeme_id`
(`slugify(term)`) is derived, never stored, and a validator requires that the term
actually yields a slug, so a target that could never become an edge endpoint fails at
generation time rather than at export.

The `resolve` stage (§5.5) later fills in `sense_id` and `confidence` for any target
whose lexeme already exists in the store — see §2.1 for why that never moves the edge's
identity. A target absent from the store simply stays unresolved at zero cost; `resolve`
never invents a lexeme to point at.

The flat edge list is still a *projection* (`Lexeme.edges()`), never stored, so a
relation and its edge cannot disagree; it now also carries `target_sense` and
`confidence`, and excludes retired senses. v1.3 stored both relations and edges
independently and they could disagree.

### 2.5 Structured examples: text plus a span, found without a model first

```
Example
├── text
└── span: (start, end) | None    half-open char offsets of the headword occurrence
```

A token-level use needs the span; retrofitting one from free example text later is
lossy — which occurrence of the word, exactly, does the example illustrate? — so v3
captures it at generation time instead. `spans.find_span` is a pure function with no
model dependency: case-insensitive whole-word match of the headword, then each
inflected form, then a hyphen/space/underscore-insensitive multi-word match; the
longest match wins, ties go to the earliest occurrence. Matching runs directly against
the original text with `re.IGNORECASE` rather than against a lowercased or
`casefold`-ed copy, because `casefold` can change a string's length (German `"ß"` →
`"ss"`) and an offset found in the folded copy would not reliably map back to the
original.

Three cases are left to the LLM fallback on purpose, not handled approximately:
irregular inflections not supplied via `forms` (`generate_forms` only produces regular,
rule-based ones), affix headwords whose leading or trailing hyphen defeats a
word-boundary match, and any text where casefolding would change length (D-16).
`docs/SCHEMA-V3.md` §5 expects more than 90% of examples to resolve deterministically;
the remainder go to the `spans` fallback stage in batches of 40.

### 2.6 Provenance is a keyed table; an `Assessment` slot travels with content

`Lexeme.provenance` is `dict[str, Provenance]`, not a list: `add_provenance` assigns ids
`p1`, `p2`, ... in insertion order and never reuses one, so a rendition's or relation's
`provenance_id` stays valid as the table grows across later enrichment runs. Each record
carries its own stage, model, tier, token usage, cost, and timestamp, because one entry
is assembled from several calls that may differ on all of those.

`Assessment` (`readability_grade`, `qa_score`, `qa_flags`, `judge_model`, `judged_at`,
`human_verified`) is a slot, not a fixed field on one object: it appears on a
`Rendition`, a `Sense`, and the `Lexeme` itself, wherever a quality signal needs to
attach to something narrower than the whole entry. `readability_grade` is where OQ-3's
reading-level measurement now lives (`REQUIREMENTS.md` § 6); v3 reserves the field
without yet committing to what populates it.

## 3. Concurrency

### 3.1 Structure

`asyncio.TaskGroup` (3.11+) owns N worker tasks pulling from an `asyncio.Queue`. Chosen
over `gather` because a failure cancels siblings deterministically and the group's exit
is a synchronisation point — no orphaned tasks writing to a half-closed ledger.

```
TaskGroup
├── worker × N          consume WorkItem, run workflow, emit LedgerRecord
└── ledger writer × 1   sole owner of the ledger file handle
```

Single-writer ledger: workers never touch the file, they put records on a queue. This is
why interleaved JSONL lines cannot occur.

### 3.2 What stops a run

A run stops on the first of: queue exhausted, budget guard tripped, wall-clock deadline,
max-entries reached, `SIGINT`, or an unhandled worker error. All of them route through
the same path — set the stop event, stop dispatching, let in-flight items finish, drain
the ledger queue, write the summary. In-flight work is never abandoned mid-write.

`SIGINT` is installed with `loop.add_signal_handler`. A second `SIGINT` re-raises for a
hard exit.

### 3.3 Rate limiting

A token-bucket limiter per (provider, model) enforcing RPM and TPM. TPM needs an estimate
of the request's token count *before* the call; we use a conservative character-based
estimate and reconcile against reported usage afterwards, carrying the delta into the next
window. Over-estimating costs throughput; under-estimating costs 429s. We over-estimate.

pydantic-ai's own `max_concurrency` / `ConcurrencyLimiter` handles in-flight request
count. We keep our limiter for RPM/TPM because that is the dimension the provider
actually rejects on, and we need the 429 accounting for the flex fallback anyway.

### 3.4 Write safety (FR-4.3, FR-4.4)

Three mechanisms, in order:

1. **In-process**: a `dict[str, asyncio.Lock]` keyed by lexeme id. Two workers in one
   process cannot touch one entry concurrently.
2. **Cross-process**: an `O_CREAT|O_EXCL` lock file next to the entry, holding the pid
   and a timestamp. Stale locks (older than a configurable TTL, pid gone) are broken.
   This is deliberately simple; the store is a local/NFS filesystem, not a database, and
   `O_EXCL` create is atomic on both.
3. **Atomic content**: write to `.<name>.<pid>.tmp` in the same directory, `fsync`,
   `os.replace`. `os.replace` is atomic within a filesystem, so a reader sees either the
   old file or the new one — never a truncated one. This is what makes FR-4.4 hold under
   `SIGKILL`.

Sharding: `store/<aa>/<ab>/<lexeme_id>.json` where `aa`/`ab` come from a hash of the id,
not from the first letters — first-letter sharding would put a sixth of English under
`s/`. Target ≤4096 files per leaf directory.

## 4. Cost architecture

### 4.1 Price table

`pricing.py` holds a versioned table: per model, per service tier, the input / cached
input / output rate per 1M tokens, with `as_of` and a source URL. It is data, and it is
tested against the set of model ids the config can select (FR-6.5), so adding a model
without adding its price is a test failure rather than a silent $0.

Cost is computed from **reported** usage:

```
cost = (input_tokens - cache_read_tokens) * in_rate
     +  cache_read_tokens                 * cached_rate
     +  output_tokens                     * out_rate
```

Note the subtraction: providers report cached tokens inside `input_tokens`, so billing
them at the full rate would overstate cost by up to 10× on a cache-heavy workload.

### 4.2 Budget guard

The guard holds the committed spend plus a reservation for in-flight work. Before
dispatching an item, the runner reserves that item's *estimated* cost; on completion the
reservation is replaced by the actual. Dispatch stops when
`committed + reserved + estimate > budget`. This prevents the overshoot you get from
checking only committed spend with N calls in flight.

The estimate is priced at `ModelPolicy.expected_output_tokens`, a measured per-stage
typical output, not at `max_tokens` (D-41). The two diverge sharply on stages whose
ceiling exists for a worst case that is rarely hit — the RENDITIONS policy sets
`max_tokens=8192` for a four-target rewrite that can need the room, but measures ~250
output tokens/call in practice — and reserving every in-flight call at the ceiling
overstates the true cost by an order of magnitude under high concurrency, refusing
dispatch far below the actual budget. This is deliberately asymmetric with the rate
limiter's own token reservation (§ 4.1-adjacent, `router.estimate_tokens`), which stays
pessimistic and reserves at `max_tokens`: a provider enforces its TPM ceiling against
the call's real token count regardless of what the call needed, so over-reserving there
only costs throughput, while under-reserving risks a 429. A refused reservation is also
now logged (`budget_reservation_refused`, with `committed_usd`, `reserved_usd`,
`estimate_usd`, and `budget_usd`), so a run that stops early is diagnosable from the log
rather than inferred from the summary's unspent balance.

### 4.3 Cost-saving defaults (FR-6.4)

| Lever | Default | Why |
|---|---|---|
| `service_tier` | `flex` | Batch-rate pricing on the synchronous API (§ RESEARCH 5–6). ~50% off standard for free, apart from latency. |
| Prompt layout | static system prompt first, volatile input last | Prompt caching is a prefix match; cached input is 10× cheaper than fresh input. |
| `openai_prompt_cache_key` | set per stage | Routes same-stage requests to the same cache, raising the hit rate. |
| Model per stage | cheapest that clears the stage's bar | The overview stage is a classification problem; the encyclopedia stage is not. One model for both overpays for one of them. |
| Batch API | above `batch_threshold` items | Same price as flex, but much higher rate ceilings for big sweeps. |
| Reasoning effort | `low` for structural stages | Sense inventory does not need deep reasoning. |

Defaults, by stage:

| Stage | Model | Effort | Rationale |
|---|---|---|---|
| overview (POS inventory, sense counts) | `gpt-5.4-nano` | low | Structural classification. |
| senses (definitions, relations) | `gpt-5.6-luna` | medium | The quality-critical stage; luna is the cheapest current-gen model. |
| renditions (reading level / register, any text field) | `gpt-5.6-luna` | low | Rewriting, with the source text supplied. |
| etymology / encyclopedia | `gpt-5.6-luna` | medium | Long-form, factual. |
| frontier classification | `gpt-5.4-nano` | low | Binary "is this a headword". |
| QA / judge | `claude-opus-5` | — | Deliberately a different family from the generator, so QA is not marking its own homework. |

At flex rates `gpt-5.6-luna` costs $0.10/$0.60 per 1M tokens. The v1.3 run spent under
$2,000 for 206K lexemes on `gpt-5-nano`; luna at flex is ~2× nano at standard, for a
materially better model.

## 5. Stages and workflows

### 5.1 Stage

A `Stage` binds a name, an output model, a prompt template, and a model policy. It owns
the retry loop: on `ValidationError` or `ModelRetry`, it re-asks with the error appended,
up to `max_attempts`. It returns a `StageResult` carrying the output, usage, cost, and
attempt count — never a bare value, because the caller must record provenance.

`StageName` covers both the content stages — `overview`, `senses`, `renditions`,
`etymology`, `encyclopedia`, `lexical_explanation` — and the structural ones v3 adds:
`classify_kind`, `tag_domain`, `resolve`, `spans`; `frontier` and `qa` are unchanged. The
rule is the same for every stage, structural or not (`docs/SCHEMA-V3.md` §5): the
context window carries only what the decision needs, static content goes in
instructions where it is cached, and effort is `low` unless the stage is writing prose.
`COST-MODEL.md` works the arithmetic per stage.

### 5.2 `generate` (FR-1)

`overview` → fan out `senses` per POS → optional `etymology`, `encyclopedia`,
`lexical_explanation` in parallel → assemble → validate → write.
Per-POS sense generation is concurrent within one entry, bounded by the same limiter.

`overview` now also decides `kind` (and the `proper_noun` block, when it applies); the
per-sense `domain` comes from the *senses* stage instead, one enum-constrained field per
`DraftSense` (`contracts.py`), not a separate call. Between the two, a freshly generated
entry never needs `classify_kind` or `tag_domain` for the common path: `generate` does
not call either stage (D-17) — both exist purely as `retrofit` passes, for entries that
predate v3, were migrated, or had a stage fail. `senses` emits typed `relations` and,
per sense, up to three `confusables` (`{term, how_they_differ}`), which become
`confusable_with` relations with their `note` already filled in. Examples come back as
plain text; `find_span` fills every span deterministically right after the call, and
only a genuine miss goes to the `spans` fallback stage, in batches of 40
(`SPAN_BATCH_SIZE`).

### 5.3 `walk` (FR-2)

```
sample seed ─► collect relation targets ─► filter ─► generate ─► (targets of new entry)
     ▲                                                                    │
     └────────────────────── frontier queue, depth-bounded ◄──────────────┘
```

The filter chain is ordered cheapest-first, and each filter records why it rejected a
candidate so the rejection set is auditable:

1. normalisation (case, whitespace, punctuation)
2. structural rejects (empty, numeric, too long, sentence-shaped)
3. store membership (already exists → not frontier)
4. run-local dedup
5. stoplist of known generation artifacts (etymology roots like `PIE *sneu-`, meta-labels)
6. *only then* the LLM headword classifier, batched

Steps 1–5 are free. On the v1.3 numbers (~2.1M raw candidates) the free steps are what
make step 6 affordable — OQ-2 asks whether step 6 is needed at all.

`strategy=domain-deficit` samples seeds from the domains furthest below the taxonomy's
target share, using `taxonomy.deficit_table` against the store's actual `DomainTag`
counts. The walk's summary reports the deficit table alongside the usual stop reason, so
a domain-balancing sweep is auditable the same way a random one is.

### 5.4 `enrich` (FR-3)

Compute the diff between requested targets and what the entry has; generate only the
difference; merge; write. Empty diff → no call, no cost, exit 0 (FR-3.5). A request is
`RenditionRequest = {field: gloss|examples|encyclopedia|explanation, levels, styles}`
(`workflows/enrich.py`); because every one of those fields is a `Renditions[T]` (§2.2),
one operation covers all four — there is no field-specific enrichment path any more.
`RenditionRequest.targets()` crosses `levels` × `styles`, defaulting either empty axis to
`(neutral,)` / `(plain,)`, so "graded definitions" (levels only) and "parallel
registers" (styles only) are the same request shape with one axis filled in.
`EnrichmentSpec.for_glosses(reading_levels=..., registers=...)` is the constructor for
the common case of one gloss-only request. Planning (`_plan`) issues exactly one model
call per `(owner, field)` that has missing targets: one call per sense for `gloss` and
`examples`, one call for the entry-level `encyclopedia` and `explanation` each. For
`examples`, only the sense's first canonical example is rewritten per target — rewriting
every canonical example would multiply output tokens by however many examples the
sense has, for a set the reader only sees one of (D-20).

`EnrichmentSpec.replace` only affects the three long-form *sections* (etymology,
encyclopedia, lexical explanation): with `replace=True`, `_add_sections` regenerates a
section's canonical text even if one already exists. It has no effect on renditions —
`_add_renditions` always computes `renditions.missing(targets())`, a pure diff, so a
rendition can be added but never overwritten through `EnrichmentSpec` (D-22).

`encyclopedia` targets default to reading levels crossed with `plain` only, not the full
register cross product, because its canonical text is long enough that output cost
scales with it (`COST-MODEL.md` §"why registers are off for it by default").

### 5.5 `resolve` (new)

Per entry, `_collect` gathers every relation whose `target.sense_id` is still `None` and
whose target lexeme exists in the store (a lookup cache avoids re-reading the same
target twice); a target whose lexeme is absent never reaches the model — it is left
unresolved at zero cost, because `resolve` cannot invent a lexeme and asking would only
spend money to learn "not found". The pending list is then chunked at
`RESOLVE_BATCH_SIZE` (40) targets per call — an entry with more than 40 unresolved,
in-store targets costs more than one call, not one call with an oversized prompt.
Context per call is the source sense's canonical gloss plus `(index, canonical gloss)`
for each candidate sense of every target in that chunk. The model answers with a
position in the numbered target/candidate lists it was shown (`target_ref`,
`sense_choice`; D-19), never `null` when it is unsure — `sense_choice: null` means "none
of these senses," recorded as `declined` rather than `resolved`. A resolved target
writes `RelationTarget.sense_id` and `.confidence` in place; `resolve` needs no
provenance idempotence marker of its own, because "already resolved" is directly visible
as `sense_id is not None` on the relation.

### 5.6 `retrofit` (new)

Runs `classify_kind`, `tag_domain`, and `spans` over an existing store, in that order —
the passes a migrated or partially-generated entry needs to reach parity with a freshly
generated one. Idempotence is a provenance marker, not just "the field looks filled,"
because `kind` is required and so cannot itself signal "not yet classified": an entry
the deterministic rule decides gets a zero-cost `Provenance` record
(`model="rule:classify_kind_deterministic"`) purely to mark it scanned, and
`_classify_kind_pass` skips any entry that already carries a `classify_kind` provenance
record, rule-written or model-written alike (D-21). `spans` reuses the same
"has a `spans` provenance record" check, but only around its paid model fallback — the
free `find_span` pass always runs over every example on every sweep, since it costs
nothing to re-check. `tag_domain` needs no marker: it sends only senses whose `domain`
is still `None`, so a second sweep over an already-tagged store sends nothing. `--only`
selects a single pass, which matters most right after a bulk migration (§8):
`classify_kind` has real work to do there (every ambiguous compound placeholder), but
`tag_domain` may not, for an entry whose legacy domain already resolved through
`LEGACY_DOMAIN_MAP`.

## 6. Failure handling

| Failure | Response |
|---|---|
| Schema validation fails | Retry with the error fed back, ≤ `max_attempts`, then mark the stage failed |
| 429 `resource_unavailable` (flex) | Exponential backoff; after N consecutive, downgrade this run to `service_tier="auto"` and log it. Not billed (§ RESEARCH 6). |
| 429 rate limit | Backoff with jitter; tighten the local limiter |
| 408 timeout | SDK retries twice; then our backoff. Flex default timeout 15 min. |
| 5xx | Backoff with jitter |
| 4xx (other) | Fail the item, do not retry |
| Budget exceeded | Stop dispatch, drain, summarise |
| Lock contention | Skip the item this pass; a later pass retries |

Failures are recorded per item in the ledger and never abort a whole run unless the run
is configured `--fail-fast`.

## 7. Testing

- Unit tests for pure logic: identity derivation, cost arithmetic, price-table coverage,
  filter chain, budget guard, token bucket.
- Workflow tests against `FunctionModel`, which returns scripted structured output — the
  full pipeline runs with zero network and deterministic assertions.
- Store tests for atomicity: kill a write mid-flight and assert no partial file; two
  concurrent writers and assert serialisation.
- Live tests marked `@pytest.mark.live`, deselected by default, run against real models
  with a $0.10 budget cap.

## 8. Migration from v1.3 and v2.0

`migrate.py` upgrades both prior shapes to the current schema; `migrate.detect_version`
picks the right path from the payload's own shape — a declared `schema_version`, or, for
the schemaless v1.3 store, the presence of `word`/`entries` versus
`headword`/`pos_entries`.

- `migrate.from_v2` upgrades this project's own previous schema: the six parallel
  relation lists (`synonyms`, `antonyms`, ...) become one typed `relations` list with
  unresolved targets; the single `gloss` string becomes the canonical `(neutral, plain)`
  rendition and `variants` become the rest of that set; `examples` become canonical
  example renditions with spans filled by `find_span`; the provenance list becomes the
  keyed table, and any record whose `stage` was `"variants"` is rewritten to
  `"renditions"` (D-13) so it still validates against the current `StageName` enum.
- `migrate.from_v13` upgrades the v1.3 working-store shape at
  `/nas4/data/workspace/curriculum/data/lexicon/` (205,996 files, one flat directory):
  `word` → `headword`, and `id` already equals the slug, so `lexeme_id` is stable;
  `entries[].senses[].definition` → the canonical gloss; `wiki_frequency` → `frequency`;
  `stopword.is_stopword` → `is_stopword` (and `kind = function_word`); the random
  per-node UUIDs and the materialised `edges[]` list are dropped, since v3 derives both
  and they carry no information the derived ids do not. v1.3 tagged the *entry*, not the
  sense, with `domain:<name>` tags, so a resolved domain is copied onto every sense of
  the migrated entry.
- Both directions never renumber a sense: source order becomes `0..n-1` and no gap or
  reorder is introduced (`POSEntry`'s contiguous-index validator would reject one).
  Neither invents a value it cannot derive: an unmappable legacy domain string lands in
  `domain_hint`, not a guessed `DomainTag`.

Two migration-time decisions exist only to keep the *output* schema-valid, not because
they are real classifications: an ambiguous multi-word headword (whitespace present, so
`classify_kind_deterministic` returns `None`) migrates as `compound` (D-12), and every
migrated proper noun gets `entity_type = other` (D-12), because neither legacy shape
typed either one. Both are placeholders for the `retrofit` `classify_kind` pass (§5.6)
to revisit — a store built by bulk migration should run a full `classify_kind` retrofit
sweep before relying on `kind`, not treat a migrated `compound` as already correct.

The reverse direction (store → v1.3 or v2, or the HuggingFace export) is not
implemented; nothing in this project depends on migrating downward.
