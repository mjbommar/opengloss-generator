# Schema v3 — specification and work plan

Date: 2026-09-02. Status: **the contract for the v3 build.** Agents implement exactly
this; deviations go in `DECISIONS.md` with a reason.

Five structural changes land together because they all touch `Sense`:

| # | Change | Why it is foundational |
|---|---|---|
| 1 | One typed `relations` list, targets as objects, resolvable to senses | Word graph → sense graph; new relation types are enum values, not schema changes |
| 2 | `kind` discriminator on `Lexeme` | The top-level node type; sampling, QA, prompts, and MWE structure branch on it |
| 3 | Controlled domain taxonomy per sense | Makes domain-balanced expansion mechanical |
| 4 | Structured examples with spans | Token-level uses need the span; retrofitting from free text is lossy |
| 5 | `Rendition[T]` generalises variants to every text field | `enrich` becomes one uniform operation |

`SCHEMA_VERSION = "3.0"`. `Lexeme.model_validate` must reject v2 payloads (extra
fields), and `migrate.py` must upgrade them.

## 1. Models

All models keep `extra="forbid"` and the alias config from `_Base`. Field names below are
exact. The `register` alias rule from D-5 still applies: attribute `style`, wire name
`register`.

```python
class RelationType(StrEnum):
    SYNONYM = "synonym";  ANTONYM = "antonym"
    HYPERNYM = "hypernym";  HYPONYM = "hyponym"
    MERONYM = "meronym";  HOLONYM = "holonym"
    DERIVATION = "derivation";  COLLOCATION = "collocation"
    CONFUSABLE_WITH = "confusable_with"     # note = how they differ
    SEE_ALSO = "see_also";  CAUSES = "causes";  ENTAILS = "entails"
    USED_WITH = "used_with";  INSTANCE_OF = "instance_of"

class RelationTarget(_Base):
    term: str                        # surface form as the model gave it
    sense_id: str | None = None      # "{lexeme_id}:{pos}:{index}" once resolved
    confidence: float | None = None  # 0..1, from the resolver; None = unresolved
    @property lexeme_id -> str        # slugify(term); derived, never stored

class Relation(_Base):
    type: RelationType
    target: RelationTarget
    note: str | None = None          # required when type is CONFUSABLE_WITH
    provenance_id: str | None = None

class LexemeKind(StrEnum):
    SIMPLEX, COMPOUND, PHRASAL_VERB, IDIOM, PROPER_NOUN, ABBREVIATION, AFFIX, FUNCTION_WORD

class EntityType(StrEnum):
    PERSON, PLACE, ORGANIZATION, WORK, EVENT, PRODUCT, SPECIES, OTHER

class ProperNounInfo(_Base):
    entity_type: EntityType
    wikidata_qid: str | None = None  # pattern ^Q[1-9][0-9]*$

class Assessment(_Base):
    readability_grade: float | None = None
    qa_score: float | None = None
    qa_flags: list[str] = []
    judge_model: str | None = None
    judged_at: datetime | None = None
    human_verified: bool = False

class Example(_Base):
    text: str
    span: tuple[int, int] | None = None   # [start, end) char offsets of the headword form
    # validator: 0 <= start < end <= len(text) when present

class Rendition[T](_Base):
    reading_level: ReadingLevel
    style: Register = Field(alias="register")
    content: T
    provenance_id: str | None = None
    assessment: Assessment | None = None
    @property key -> (reading_level, style)

class Renditions[T](RootModel[list[Rendition[T]]]):
    # validator: keys unique
    def canonical(self) -> Rendition[T] | None      # (NEUTRAL, PLAIN)
    def has(self, level, style) -> bool
    def missing(self, targets: Iterable[tuple[level, style]]) -> list[tuple]
    def get(self, level, style) -> Rendition[T] | None
    def add(self, rendition) -> None                 # raises on duplicate key

class Sense(_Base):
    index: int
    gloss: Renditions[str]                 # canonical (NEUTRAL, PLAIN) is REQUIRED
    examples: Renditions[Example]          # canonical examples are (NEUTRAL, PLAIN); several allowed —
                                           #   uniqueness is on (level, style, content.text)
    relations: list[Relation] = []
    domain: DomainTag | None = None
    secondary_domains: list[DomainTag] = []
    domain_hint: str | None = None         # legacy free text kept only as input to the retag stage
    concept_id: str | None = None          # reserved for v4 concept nodes
    assessment: Assessment | None = None
    retired: bool = False
    # helpers: canonical_gloss() -> str; relations_of(type) -> list[Relation];
    #          relation_targets() -> set[str]

class Lexeme(_Base):
    schema_version: str = "3.0"
    lexeme_id: str; headword: str; language: str = "en"
    kind: LexemeKind
    proper_noun: ProperNounInfo | None = None     # validator: present iff kind == PROPER_NOUN
    status: EntryStatus
    pos_entries: list[POSEntry]
    etymology: Etymology | None = None
    encyclopedia: Renditions[str] = []             # canonical is (NEUTRAL, PLAIN)
    lexical_explanation: Renditions[str] = []
    is_stopword: bool = False                     # kept; kind == FUNCTION_WORD implies True
    frequency: float | None = None
    discovered_from: str | None = None
    provenance: dict[str, Provenance] = {}        # keyed table; ids "p1", "p2", ... per entry
    assessment: Assessment | None = None
    created_at, updated_at
    # helpers: add_provenance(p) -> id; edges() -> list[Edge] (from relations; retired senses excluded);
    #          relation_targets(); sense_count(); iter_senses(); rendition_ids()
```

`Edge` gains `target_sense: str | None` and `confidence: float | None`; `edge_id` is
unchanged (`{source_sense}-{type}->{target_term_slug}`), so ids are stable across
resolution.

`identity.rendition_id(owner_id, level, style)` replaces `variant_id`; owner_id is a
sense id for gloss/examples, `"{lexeme_id}:encyclopedia"` / `"{lexeme_id}:explanation"`
for entry-level fields.

## 2. Taxonomy (`taxonomy.py`)

`DomainTag(StrEnum)` with dotted values `root.leaf`, ~15 roots, 8–14 leaves each
(most roots hold 8–12; `everyday_life` and `people_society` carry 14 — D-44, the two
roots with the largest `.general` residue on the 10K core), ~160 leaves total. Roots
(fixed): `arts`, `business`, `education`, `everyday_life`, `health`, `history`,
`humanities`, `language`, `law_government`, `mathematics`, `nature`, `people_society`,
`science`, `sports_recreation`, `technology`. Each root also has a `.general` leaf for
"in this domain but no finer". Helpers: `root_of(tag)`, `leaves_of(root)`, `ROOTS`,
`LEAF_COUNT`, `TAXONOMY_VERSION` (bumped whenever a leaf is added; recorded in the
`tag_domain` provenance `note` so `hygiene` step (d) can tell a stale `.general` verdict
from a current one, D-44), `TAXONOMY_PROMPT_BLOCK` (a byte-stable string listing every
leaf with a five-word gloss, for inclusion in *instructions* so it is prompt-cached),
`LEGACY_DOMAIN_MAP: dict[str, DomainTag]` mapping the v1.3 free-text domains ("general
academic", "history", "geography", "art", "civics", "biology", …). Rule: never add a
root without updating `ROOTS`; a test asserts every enum value's root is in `ROOTS`.
Leaves may be added (never renamed or removed — stored data references them by value).

## 3. Spans (`spans.py`)

`find_span(text, headword, forms: Iterable[str] = ()) -> tuple[int, int] | None`.
Deterministic: case-insensitive whole-word match of the headword, then each inflected
form (from `Morphology`), then hyphen/space-insensitive multi-word match; longest match
wins, first occurrence on ties. Pure function, no model. `annotate_examples(entry)`
fills every `Example.span` that is `None` and returns the count still unresolved; those
go to the LLM fallback stage in batches of 40.

## 4. Migration (`migrate.py`)

* `from_v2(payload: dict) -> Lexeme` — six lists → `relations` (`sense_id=None`,
  `confidence=None`); `gloss: str` → `Renditions` canonical; `variants` → gloss
  renditions; `examples: list[str]` → canonical example renditions with `span` via
  `find_span`; `domain: str` → `LEGACY_DOMAIN_MAP` else `domain_hint`; `kind` from
  `classify_kind_deterministic` (see § 5); provenance list → table; entry-level
  `encyclopedia_entry`/`lexical_explanation` strings → canonical renditions.
* `from_v13(payload: dict) -> Lexeme` — the v1.3 working-store shape at
  `/nas4/data/workspace/curriculum/data/lexicon/<word>.json` (inspect `alluding.json`
  and `3d_model.json`). `word` → headword; `entries[].senses[].definition` → canonical
  gloss; drop random UUIDs and `edges[]` (re-derived); `wiki_frequency` → `frequency`;
  `stopword` → `is_stopword`/`FUNCTION_WORD`.
* Both are lossless in the direction that matters and never renumber a sense.
* `detect_version(payload) -> "1.3" | "2.0" | "3.0"`.

## 5. Stages, and the cost/quality decision for each

The rule: **the context window carries only what the decision needs, static content goes
in instructions (cached), and effort is `low` unless the stage writes prose.**

| Stage | Context per call | Model | Effort | Batching | Notes |
|---|---|---|---|---|---|
| `classify_kind` | deterministic first (whitespace → compound/phrasal/idiom candidate; leading capital + not sentence-initial → proper noun; ≤3 letters all-caps → abbreviation; leading/trailing hyphen → affix; stoplist → function word; lowercase headword whose own prose capitalises it non-sentence-initially ≥2 times and more often than not → proper noun, D-26); residue = each term plus one ≤120-char gloss snippet | `gpt-5.4-nano` | low | 50 terms/call | Expect >85% resolved deterministically; measure and log the ratio |
| `tag_domain` | headword + every sense's canonical gloss (~100–300 tokens); the full leaf list lives in **instructions** (~1.5K tokens, cached) | `gpt-5.4-nano` | low | 1 call per entry, all senses | Output is the enum, so structured output constrains it; no free text |
| `resolve` | source sense gloss + for each unresolved target that exists in the store: `(index, canonical gloss)` per target sense | `gpt-5.4-nano` | low | 1 call per source **entry**, ≤40 targets/call | Targets absent from the store stay `sense_id=None` at zero cost; ~5K tokens/call worst case |
| `spans` (fallback) | example text + headword + forms | `gpt-5.4-nano` | low | 40 examples/call | Only examples the deterministic finder could not place |
| `renditions` (gloss, examples, explanation) | canonical + existing renditions of that one field | `gpt-5.6-luna` | low | 1 call per (sense, field) covering all missing targets | Writing prose, so luna; low effort because the source text is supplied |
| `renditions` (encyclopedia) | the 300–400-word canonical | `gpt-5.6-luna` | low | 1 call per level-set | The expensive one: output ≈ N × source length. Default target set for encyclopedia is reading levels only, not registers |
| `senses` (existing) | + `confusables: [{term, how_they_differ}] ≤3` | unchanged | | | Adds ~60 output tokens per sense |

Config: `StageName` gains `CLASSIFY_KIND`, `TAG_DOMAIN`, `RESOLVE`, `SPANS`, and
`VARIANTS` is renamed `RENDITIONS`. `AppConfig` gains
`encyclopedia_rendition_targets` (default: reading levels × `plain`) separate from
`default_reading_levels`/`default_registers`.

## 6. Workflows

* `generate` — overview stage also emits `kind` (+ `proper_noun` block) and per-sense
  `domain`; senses stage emits `relations` typed, examples as text (spans filled by
  `find_span` post-hoc, LLM fallback only on miss), and confusables.
* `enrich` — one operation: `RenditionRequest = {field: gloss|examples|encyclopedia|explanation, levels, styles}`; compute missing, one call per (owner, field).
* `resolve` (new) — per entry, resolve unresolved relation targets whose lexeme exists.
* `retrofit` (new) — runs `classify_kind`, `tag_domain`, `spans` over a store, idempotent
  (skips what is already filled), with `--only` to select a pass.
* `walk` — `strategy=domain-deficit`: choose seeds from the domains furthest below the
  taxonomy's target share; report the deficit table in the summary.

## 7. File ownership per agent

| Round | Agent | Model | Files |
|---|---|---|---|
| 1 | A | opus | `schema.py`, `identity.py`, `migrate.py`, `tests/test_schema.py`, `tests/test_migrate.py`, `tests/conftest.py::make_entry` only |
| 1 | B | sonnet | `taxonomy.py`, `tests/test_taxonomy.py` |
| 1 | C | sonnet | `spans.py`, `tests/test_spans.py` |
| 2 | D | opus | `contracts.py`, `prompts.py`, `config.py`, `stages.py`, `workflows/*`, `tests/conftest.py`, `tests/test_generate.py`, `tests/test_enrich.py`, `tests/test_resolve.py`, `tests/test_retrofit.py` |
| 2 | E | sonnet | `docs/DESIGN.md`, `docs/REQUIREMENTS.md`, `docs/DECISIONS.md`, `docs/COST-MODEL.md`, `README.md` |
| 3 | F | sonnet | `workflows/walk.py`, `cli.py`, `tests/test_walk.py`, `tests/test_cli.py` |

Round-1 agents B and C run `pytest --noconftest tests/test_<theirs>.py` because
`conftest.py` imports the schema, which A is changing underneath them.

## 8. Implementation notes (2026-09-02)

The build deviated from this contract's exact wording in several places, each with a
reason. Every deviation is recorded in `docs/DECISIONS.md`; this section only indexes
them so the contract does not silently disagree with the shipped code.

| Decision | One-line summary |
|---|---|
| D-10 | `Lexeme.rendition_ids()` omits example renditions; an example needs `(sense_id, level, register, text)` to address, not a single id. |
| D-11 | `classify_kind_deterministic` treats an internal hyphen with no surrounding whitespace as `compound`, before the whitespace-ambiguity check reaches the model. |
| D-12 | Migration placeholders: ambiguous residue migrates as `compound`, migrated proper nouns get `entity_type=other` — both flagged for a `retrofit` sweep, not real classifications. |
| D-13 | Legacy provenance `stage: "variants"` is rewritten to `"renditions"` during migration; the original label is not separately preserved. |
| D-14 | A legacy domain string exactly equal to a taxonomy root maps to that root's `.general` leaf, checked after `LEGACY_DOMAIN_MAP`. |
| D-15 | `"geography"`, `"psychology"`, `"economics"` are documented best-fit (not exact-fit) legacy domain mappings, and likelier `tag_domain` retrofit candidates. |
| D-16 | `spans.py` deliberately leaves three cases to the LLM fallback: irregular inflections, hyphen-edge affixes, and casefolding-length changes. |
| D-17 | `DraftOverview.domain` stays free text (→ `Sense.domain_hint`); the controlled tag comes from the enum-constrained `DraftSense.domain`, so `generate` never calls `tag_domain` — it is retrofit-only. |
| D-18 | `DraftKindVerdict` carries no `entity_type`; a `classify_kind` promotion to `proper_noun` gets `entity_type=other`, same placeholder pattern as D-12. |
| D-19 | Structural contracts (`sense_ref`, `target_ref`/`sense_choice`, `example_ref`) answer with a list position, not an id — cheaper, and cannot disagree with the list the model was shown. |
| D-20 | `enrich`'s `examples` rendition rewrites only the sense's first canonical example per target, not every canonical example. |
| D-21 | `classify_kind` and the `spans` LLM fallback are idempotent via a provenance marker; the deterministic `classify_kind` branch writes a zero-cost `model="rule:classify_kind_deterministic"` record. |
| D-22 | `EnrichmentSpec.replace` only forces regeneration of long-form sections (etymology, encyclopedia, lexical explanation); rendition requests are always additive, never a replace. |
| D-23 | D-7's budget-propagation rule is restated explicitly at every sequential (non-`gather`) call site in `resolve.py` and `retrofit.py`, not only where `gather` makes it load-bearing. |
| D-24 | `attach_long_form` (etymology/encyclopedia/lexical-explanation attachment) is defined once in `generate.py` and imported by `enrich.py`, so both workflows agree on what "the encyclopedia section" means. |
| D-25 | `PROMPT_VERSION` is bumped to `"2"` for the v3 instruction rewrite, so provenance alone distinguishes v3-era content from anything recorded under the pre-v3 prompt text. |
| D-26 | A lowercase headword is promoted to `proper_noun` when the entry's own prose capitalises it at least twice outside sentence-initial position and more often than not; one capitalised mention without a majority goes to the residue instead of asserting `simplex`. |
| D-32 | `PartOfSpeech` gets `UPOS_MAP`/`LEXINFO_MAP` and a `.upos` property (export crosswalk only, enum unchanged); `upos_for(entry, pos)` applies UD's `PROPN` rule at export time rather than storing it. |
| D-33 | `EtymologySegment.language_code` (optional `str`) validates ISO 639-3 shape or the `ine-pro`/`gem-pro` reconstructed-language exceptions; the field is added ahead of population — A2's etymology-code retrofit pass is deferred. |
| D-34 | `ReadingLevel.crosswalk` / `READING_LEVEL_CROSSWALK` document the CCSS/Lexile/CEFR/MARC crosswalk; `readability.grade_band` now reads its bands from `schema.FK_BANDS`, the same table the crosswalk sits beside, so the two cannot drift apart. Band numbers are unchanged. |
| D-35 | `RelationType.namespace` (`"wn"`/`"og"`) plus `WN_RELATION_MAP`/`SKOS_RELATION_MAP`, and `EntityType`'s `ONTONOTES_MAP`/`SCHEMA_ORG_MAP`, are export-only crosswalks per the § 8 reconciliation in `docs/STANDARDS-PLAN.md` — no enum rename, no retrofit for either. |
| D-36 | `QAFlag(StrEnum)` (MQM Core plus four `og.`-prefixed project flags) replaces `Assessment.qa_flags`'s free-text `list[str]`; `enrich.py` writes `OG_READABILITY_MISS` on a rendition that still misses its band after retry, `audit.py` only counts it. `taxonomy.py` gains `LCC_MAP`/`IPTC_MAP` (export crosswalk, `history` has no IPTC top-level analogue). |
