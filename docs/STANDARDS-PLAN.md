# Plan — grounding fields in external standards

Date: 2026-09-02. Status: **plan, reconciled with `STANDARDS.md` § 11 (see § 8)**. Companion to `STANDARDS.md` (the sourced value lists
and crosswalks — every value list below is verified against it before implementation) and `REGISTERS.md` (already done for
`Register`, implemented as D-27).

## 0. Rules that apply to every item

1. **Store the standard's code when the mapping is lossless in our direction; store our
   value plus an export-time mapping when our vocabulary is richer.** We do not throw
   away distinctions to fit a standard, and we do not invent a vocabulary where a
   standard already covers ours.
2. **Never change an identifier.** `lexeme_id`, `sense_id`, `rendition_id`, `edge_id`
   (D-1) are untouched by everything here. An enum *value* may be renamed only with a
   `_missing_` hook that accepts the legacy spelling (the D-27 pattern), so existing
   JSON still loads.
3. **Deterministic first, model second.** A crosswalk is a table. Only where a standard
   is *finer* than our data (meronym subtypes, entity subtypes) does a nano pass run,
   and only over the affected subset.
4. **Every adoption is a decision-log entry** (`DECISIONS.md`, D-28 onward) citing the
   URL in `STANDARDS.md`, and a line in `SCHEMA-V3.md` § 8.
5. **Gates unchanged**: ruff, ty, pytest green; `filterwarnings=error`.

Cost figures use the measured rates from `COST-MODEL.md` § "Measured" and
`CORE-DIARY.md` (nano ≈ $0.0003 per short call at flex; 10K core / 206K full store).

## 1. The table, turned into work

| # | Field | Action class | Model calls? | Core cost | Full-store cost | Phase |
|---|---|---|---|---|---|---|
| 1 | `PartOfSpeech` → UPOS + LexInfo | mapping (property + export) | no | $0 | $0 | A |
| 3 | `EtymologySegment.language` → ISO 639-3 | stored code + display name | mostly no; nano for unmapped residue | ~$0.05 | ~$1 | A |
| 7 | `frequency` → Zipf | stored derived value | no | $0 | $0 | A |
| 8 | `Provenance` → PROV-O | export mapping | no | $0 | $0 | A |
| 10 | `concept_id` → ILI | reserved slot, format only | no | $0 | $0 | A |
| 6 | `ReadingLevel` → CCSS/Lexile/MARC/CEFR | crosswalk table, docs + property | no | $0 | $0 | A |
| 2 | `RelationType` → GWA WordNet + SKOS | enum refinement + `og:` namespace | yes, meronym/holonym split only | ~$0.3 | ~$6 | B |
| 4 | `EntityType` → OntoNotes/Schema.org | enum refinement + retag | yes, proper nouns only (1,043 in core) | ~$0.3 | ~$6 | B |
| 9 | `Assessment.qa_flags` → MQM | closed enum | no (affects future judge calls) | $0 | $0 | B |
| 5 | `DomainTag` roots → LCC + IPTC | crosswalk table + export | no | $0 | $0 | C |

**Phase A** is additive and free: it can ship as one pull request without touching the
rendition sweep. **Phase B** changes enums that existing data uses and needs a retrofit
pass. **Phase C** is documentation and export only.

## 2. Phase A — additive, zero regeneration

### A1. `PartOfSpeech` ↔ UPOS / LexInfo

- Add `UPOS_MAP: dict[PartOfSpeech, str]` and `LEXINFO_MAP` in `schema.py`
  (`noun→NOUN`, `verb→VERB`, `adjective→ADJ`, `adverb→ADV`, `pronoun→PRON`,
  `preposition→ADP`, `conjunction→CCONJ` *(lossy: SCONJ collapsed — record)*,
  `determiner→DET`, `interjection→INTJ`, `numeral→NUM`), plus `PartOfSpeech.upos`
  property. Proper nouns: `kind == PROPER_NOUN` ⇒ export `PROPN` regardless of stored
  POS — a rule, not a stored value.
- Decide whether to **add** `auxiliary` and `particle` as POS values. Recommendation:
  no for v3 (they never occur as dictionary headwords in our data); note in D-28.
- Tests: map covers every member; `upos` round-trips through `opengloss-graph`'s
  LexInfo emitter (that project already uses `lexinfo:` — align the two tables).
- Files: `schema.py`, `tests/test_schema.py`, `DECISIONS.md` D-28.

### A2. `EtymologySegment.language` → ISO 639-3

- Schema: add `language_code: str | None` (ISO 639-3, three lowercase letters, or the
  agreed non-ISO tags for reconstructed languages — `STANDARDS.md` § 3 says what
  Wiktionary/Glottolog use for Proto-Indo-European and Proto-Germanic; adopt the
  Wiktionary codes `ine-pro`, `gem-pro` as an explicit exception list). Keep `language`
  as the display name.
- `etymology_codes.py`: a deterministic table of the ~40 display names that cover
  >95% of segments (measure on the core first: histogram of `language` strings),
  case/whitespace-insensitive, with aliases ("Anglo-Norman", "Anglo-French"; "Old
  Norse", "Norse"). Unmapped residue → one nano call per batch of 50 strings
  (`StageName.HYGIENE` policy) returning the code, cached in a JSON lookup so the same
  string is never asked twice.
- Retrofit: extend the `hygiene` pass with step (e) "etymology codes"; idempotent by
  `language_code is not None`.
- Tests: table coverage on the core histogram fixture; alias handling; residue path
  via the scripted model.
- Files: `schema.py`, new `etymology_codes.py`, `workflows/retrofit.py`,
  `migrate.py` (apply on import), tests, D-29.

### A3. `frequency` → Zipf

- Store both: keep `frequency` (raw count, as now) and add `zipf: float | None` computed
  as `log10((count + 1) / corpus_tokens * 1e9)`; record `frequency_corpus: str` and
  `frequency_corpus_tokens: int` on the entry so the number is reproducible (the v1.3
  Wikipedia corpus size must be recovered from `curriculum/pipelines/wiki_frequency.py`
  or recomputed — a task in itself; until then `zipf` stays `None`).
- Use: the `walk` sampler and the core-lexicon script rank by Zipf instead of raw count;
  the pedagogical band (`difficulty`, roadmap #10) reads it.
- Files: `schema.py`, `scripts/core_lexicon.py`, `migrate.py`, tests, D-30.

### A4. `Provenance` → PROV-O (export only)

- No schema change. In `opengloss-graph` (the RDF exporter), emit each `Provenance`
  record as a `prov:Activity` (`prov:startedAtTime = generated_at`,
  `prov:wasAssociatedWith` a `prov:SoftwareAgent` for the model id, `og:promptVersion`,
  `og:costUsd`), and each rendition/relation with `prov:wasGeneratedBy`.
- Deliverable here: a mapping table in `STANDARDS.md` § 8 and a Turtle example; the
  code lives in the exporter, not this repo.

### A5. `concept_id` → ILI

- Reserve the format now: `concept_id` values are either `ili:iNNNNNN` (an existing ILI
  id, once aligned) or `og:c-<hash>` (a project concept with no ILI counterpart).
  Add a validator that accepts exactly those two shapes. No population in v3.
- Files: `schema.py`, tests, D-31.

### A6. `ReadingLevel` crosswalk

- Add `READING_LEVEL_CROSSWALK: dict[ReadingLevel, LevelCrosswalk]` with fields
  `ccss_band`, `lexile_band`, `cefr`, `marc_audience`, `approx_age` from
  `REGISTERS.md` § 7c and `STANDARDS.md` § 6; a `ReadingLevel.crosswalk` property.
- The readability bands in `readability.grade_band` are re-derived from the CCSS/Lexile
  table so the check and the documentation cannot disagree (test asserts they match).
- Files: `schema.py`, `readability.py`, tests, D-32.

## 3. Phase B — enum refinement with a retrofit pass

### B1. `RelationType` → GWA WordNet inventory + `og:` namespace

- Keep the enum but (a) split `meronym`/`holonym` into `mero_part`, `mero_member`,
  `mero_substance` / `holo_part`, `holo_member`, `holo_substance`; (b) rename
  `see_also → also_see` (WordNet spelling) with a `_missing_` alias; (c) add
  `instance_hyponym` as the inverse of `instance_of` (and consider `instance_of →
  instance_hypernym` with alias); (d) mark `confusable_with`, `used_with`,
  `collocation`, `derivation`? (check: `derivation` *is* WordNet's `derivation`) as
  project-specific via a `RelationType.namespace` property returning `"wn"`, `"skos"`,
  or `"og"`.
- Retrofit pass `relation_types`: for every `meronym`/`holonym` relation, one nano call
  per entry classifying its meronymy subtype from the two glosses (batched; context =
  source gloss + target term; ~$0.0003 each). Everything else is a rename.
- Export: `opengloss-graph` maps `wn` types to `wn:` properties and `skos` to
  `skos:broader/narrower/related`; `og` types keep the `og:` ontology.
- Tests: every member has a namespace; legacy values load; retrofit splits a
  fixture meronym; edge ids unchanged by the rename (assert on a fixture).
- Files: `schema.py`, `workflows/retrofit.py`, `contracts.py` (subtype contract),
  `audit.py` (artifact rules unchanged), tests, D-33.
- Cost: core ~1,000 meronym/holonym relations → ~$0.3; full store ~$6.

### B2. `EntityType` → OntoNotes types (stored) + Schema.org (export)

- Replace the 8-value enum with the OntoNotes 18 (verify the exact list in
  `STANDARDS.md` § 4): `PERSON, NORP, FAC, ORG, GPE, LOC, PRODUCT, EVENT, WORK_OF_ART,
  LAW, LANGUAGE, DATE, TIME, PERCENT, MONEY, QUANTITY, ORDINAL, CARDINAL`, keeping
  `OTHER` as the placeholder. `_missing_` maps legacy `place→GPE` *(lossy: GPE vs LOC
  needs the retag)*, `organization→ORG`, `work→WORK_OF_ART`, `species→OTHER` *(no
  OntoNotes type; keep a project value `SPECIES` in the `og` namespace rather than lose
  it)*.
- `SCHEMA_ORG_MAP` for export (`PERSON→Person`, `ORG→Organization`, `GPE/LOC/FAC→Place`,
  `WORK_OF_ART→CreativeWork`, `PRODUCT→Product`, `EVENT→Event`).
- Retrofit pass `entity_types`: every `proper_noun` entry whose `entity_type` is
  `OTHER` (all migrated ones — D-12) gets one nano call with the canonical gloss +
  first encyclopedia paragraph, structured output constrained to the enum. Batch 20
  per call. Also request the Wikidata QID *only if the model is confident*; verify QIDs
  against the Wikidata API in a separate free-ish pass before storing (never store an
  unverified QID).
- Tests: legacy mapping, retag via scripted model, QID pattern validator unchanged.
- Files: `schema.py`, `contracts.py`, `workflows/retrofit.py`, `migrate.py`, tests,
  D-34.
- Cost: core 1,043 proper nouns → ~$0.3; full store ~20K → ~$6.

### B3. `Assessment.qa_flags` → MQM core typology

- Replace `list[str]` with `list[QAFlag]` where `QAFlag(StrEnum)` carries the MQM
  core error types (verify in `STANDARDS.md` § 9: accuracy/mistranslation,
  accuracy/omission, accuracy/addition, terminology, fluency/grammar,
  fluency/spelling, style/register, style/awkward, locale, design…), plus project
  flags in an `og.` prefix (`og.headword_initial`, `og.artifact_relation`,
  `og.readability_miss`). The free consistency checks in `audit.py` and the readability
  miss in `enrich.py` start writing these flags, so the QA vocabulary is populated
  before the judge stage exists.
- Files: `schema.py`, `audit.py`, `workflows/enrich.py`, tests, D-35.

## 4. Phase C — documentation and export

### C1. `DomainTag` roots → LCC + IPTC crosswalk

- `taxonomy.py` gains `LCC_MAP: dict[str, tuple[str, ...]]` (root → LCC main classes,
  e.g. `science → (Q,)`, `nature → (Q, S)`, `law_government → (J, K)`,
  `history → (C, D, E, F)`, `arts → (M, N)`, `language → (P,)`,
  `humanities → (B, P)`) and `IPTC_MAP` (root → IPTC Media Topic top-level codes).
  Verify class letters against `STANDARDS.md` § 5; flag one-to-many rows explicitly.
- Export: `opengloss-graph` emits `dcterms:subject` with the LCC class URI alongside
  the `og:domain` leaf.
- Tests: every root has both entries; no LCC letter outside A–Z.
- Files: `taxonomy.py`, tests, D-36; exporter change tracked in `opengloss-graph`.

## 5. Order of execution and dependencies

```
A1 A3 A5 A6 ──────────────┐   (independent, one PR, no data change)
A2 ───────────────────────┤   (adds hygiene step e; run once over the core)
                          ▼
B1 → retrofit relation_types      (needs A1's namespace idea for `og:` types)
B2 → retrofit entity_types        (independent of B1)
B3 ───────────────────────────    (flags start being written; no retrofit)
C1 ───────────────────────────    (docs + taxonomy constants; any time)
A4 ───────────────────────────    (exporter repo; after B1 so relation namespaces exist)
```

Sequencing against the core work: Phase A can land during the current rendition
sweep (it changes no field the sweep writes). B1/B2 retrofit passes run **after** the
sweep, over the core only, and are idempotent by marker like the others.

## 6. Acceptance criteria

1. Every enum member of `PartOfSpeech`, `RelationType`, `EntityType`, `Register`,
   `ReadingLevel` has a documented external mapping or an explicit `og` namespace
   marker; a test enumerates each enum and asserts this.
2. `audit` reports: % of etymology segments with `language_code`; % of
   `meronym/holonym` relations subtyped; % of proper nouns with a non-`OTHER`
   entity type; % of entries with `zipf`.
3. Legacy JSON from every earlier schema version (v1.3 raw, v2, v3.0 pre-plan) loads
   without error (extend `tests/test_migrate.py` fixtures with the pre-plan spellings).
4. `STANDARDS.md` cites a URL for every list; `DECISIONS.md` has D-28…D-36.
5. Core cost of all retrofit passes in this plan ≤ $1; full store ≤ $15.

## 7. Explicitly not doing

- Replacing our 150 domain leaves with LCSH or IPTC leaves: their granularity is wrong
  for a learner's dictionary (LCSH has ~400K headings), and the crosswalk at root level
  gives the citability without the churn.
- Adopting UD morphological features as stored values: English inflection is fully
  covered by the six `Morphology` fields; a UD feature bundle is an export view.
- A third rendition-key axis for genre (D-27 keeps `marketing` on the register axis).
- Any standard for collocations or `lexical_explanation`.

## 8. Reconciliation with `STANDARDS.md` (research verdicts, 2026-09-02)

The sourced research is more conservative than § 3 above on two items, and it is right:

| Item | § 3 said | Research verdict | Plan now |
|---|---|---|---|
| B1 `RelationType` | split meronymy, rename, retrofit | "mostly aligns with GWA/WN-LMF; `confusable_with`, `used_with`, `collocation` have no standards home → `og:` namespace" | **Namespace property + export map only.** No enum rename, no retrofit. Meronymy subtyping is deferred to a v4 item with its own cost case. |
| B2 `EntityType` | adopt OntoNotes 18 as stored values, retag | "narrower proper-noun-only field vs. OntoNotes' full NER tagset → export mapping" | **Keep our 8 values; add `ONTONOTES_MAP` and `SCHEMA_ORG_MAP` for export.** Retag of migrated `OTHER` placeholders stays (it fills *our* enum, D-12), ~$0.3 on the core. |
| A5 `concept_id` | `ili:` / `og:c-` shapes | "additive `Sense.ili_id`, populated post hoc against Open English WordNet, never model-generated" | The `ili:` shape on `concept_id` is implemented (D-29); an ILI link is never model-generated — agreed and recorded. |

Confirmed as stored values: Zipf (A3, D-28 — fields in place, value pending the corpus
size, which the v1.3 pipeline never recorded), ISO 639-3 etymology codes (A2), MQM
`QAFlag` (B3). Everything else is export-time. Net effect on cost: Phase B drops from
~$0.6 to ~$0.3 on the core, and the only retrofit pass added is A2's etymology codes.
