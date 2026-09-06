# Decision log

One entry per decision that a future reader might otherwise reverse without knowing why.
Newest last.

## D-1 (2026-09-02) — Identifiers are derived from structure, never random

**Context.** v1.3 minted `uuid4()` per node at generation time; the HF export dropped
them; the published datasets cannot be joined back to the working store below lexeme
level.

**Decision.** `lexeme_id = slug(headword)`, `sense_id = lexeme:pos:index`,
`variant_id = sense#level/register`, `edge_id = sense-relation->target`. Sense order is
preserved across regenerations; deletion is a tombstone (`retired`), not a removal,
because ids are positional.

**Consequence.** Exports may omit ids entirely. `POSEntry` rejects non-contiguous sense
indices at validation time.

## D-2 (2026-09-02) — Definitions are a variant set, not a string

**Decision.** `Sense.gloss` is canonical and equals the `(neutral, plain)` variant;
`Sense.variants` holds `(reading_level, register)`-keyed rewrites, unique per pair.

**Consequence.** Reading-level and register expansion is an *enrichment*, not a new
dataset. All missing variants for one sense are produced in a single call (FR-3.4).

## D-3 (2026-09-02) — `service_tier="flex"` is the default

**Evidence.** The OpenAI pricing page (fetched 2026-09-02) lists Flex at exactly Batch
rates for every model in our table; the flex guide says caching stacks on top.

**Decision.** Default every OpenAI stage to flex, 15-minute timeout, and downgrade the
*run* to `auto` after `max_flex_429s` consecutive `resource_unavailable` rejections
(which are unbilled). Priced at flex rates in the cost table.

## D-4 (2026-09-02) — Cost is computed locally, `usage.cost` is a cross-check only

**Evidence.** `RunUsage.cost` in pydantic-ai 2.37 is `None` for the models we default
to (its bundled price data lags). Cached tokens are reported *inside* `input_tokens`.

**Decision.** `pricing.py` is the source of truth: versioned, dated, sourced, and
unit-tested against every model the config can select. `estimate_cost` subtracts cached
tokens from input before applying the fresh rate. A model with no price row is a
configuration error.

## D-5 (2026-09-02) — The Python attribute for a variant's register is `style`

**Problem found by test.** Naming a Pydantic field `register` makes Pydantic pick up
`ABCMeta.register` (present on every model class via the metaclass) as an implicit
default. The field silently becomes *optional* and JSON-schema generation fails with
"non-serializable default". The `UserWarning` Pydantic emits is the only visible
symptom, and it looks cosmetic.

**Decision.** Wire name stays `register` (via `Field(alias="register")`, with
`validate_by_name` / `serialize_by_alias` on the base config). Python attribute is
`style`. Rejected alternative: suppressing the warning — that would have left the field
optional.

## D-6 (2026-09-02) — ruff `TC` rules are off

**Reason.** They move annotation-only imports into `TYPE_CHECKING` blocks. Pydantic
resolves field annotations at runtime, so that breaks model construction. Documented
inline in `pyproject.toml`.

## D-7 (2026-09-02) — Budget stops propagate out of per-stage `gather`

**Problem found by test.** Stage fan-out uses `asyncio.gather(return_exceptions=True)`
so one failed stage degrades the entry to `partial` rather than aborting it. That
correctly swallows schema and transport failures — but it also swallowed
`BudgetExceededError`, so a walk that hit its ceiling reported `frontier_exhausted` and
kept dispatching (the guard refused every call, so no money was lost, but the run's
stop reason was wrong and it wasted time).

**Decision.** `_reraise_budget_stop()` after every `gather` in `generate` and `enrich`;
`walk` catches it at both the generation and frontier-expansion sites and stops with
`stop_reason="budget"`. Budget is a run-level condition, never a per-stage failure.

## D-8 (2026-09-02) — Free filters before the LLM classifier, always

**Evidence.** The v1.3 gap-fill scanned ~2.1M raw relation targets.

**Decision.** `FilterChain` runs normalisation, structural rejects, store membership,
run-local dedup, and the artifact stoplist before anything reaches a model; the
classifier is batched 40 candidates per call and a classifier *failure* keeps the batch
rather than dropping it. Every rejection carries its reason into the run summary.
Open question OQ-2 in `REQUIREMENTS.md` asks whether the classifier earns its cost at all.

## D-9 (2026-09-02) — Hash sharding, not first-letter sharding

**Evidence.** The v1.3 store had 205,996 files in one directory; `du` on it takes over
ten minutes on NFS. First-letter sharding would put roughly a sixth of English under `s/`.

**Decision.** Two levels of two-hex-character shards from `blake2b(lexeme_id)`. Tested to
spread `s*` words across >50 buckets.

## D-10 (2026-09-02) — `Lexeme.rendition_ids()` omits example renditions

**Context.** A derivable id lets a consumer address one rendition without a lookup
(D-1). For a gloss or a prose section, `(owner_id, reading_level, register)` is unique,
so `rendition_id` is a real identifier. Examples are different: `Renditions[Example]`'s
uniqueness key includes `content.text` specifically because several canonical examples
may legitimately coexist under the same `(reading_level, register)` — so that key alone
does not pick out one example.

**Decision.** `Lexeme.rendition_ids()` enumerates ids only for sense glosses and the two
entry-level prose fields (`encyclopedia`, `lexical_explanation`); example renditions are
left out entirely rather than given a synthetic, order-dependent id.

**Consequence.** Addressing one example rendition requires `(sense_id, reading_level,
register, text)`, not a single id. A future export that needs a stable per-example
identifier will have to mint one explicitly; v3 does not attempt it.

## D-11 (2026-09-02) — Internal hyphen added to the deterministic `kind` rules

**Context.** `docs/SCHEMA-V3.md` §5 lists the deterministic `classify_kind` rules but
does not explicitly say what happens to a headword with an internal hyphen and no
leading/trailing one (e.g. "mother-in-law"). Falling through to the whitespace-ambiguity
check would route every hyphenated compound to the batched classifier, but a
hyphen-joined multi-word form is not actually ambiguous the way a space-separated one is
(which could be compound, phrasal verb, or idiom).

**Decision.** `classify_kind_deterministic` treats an internal hyphen with no
surrounding whitespace as `LexemeKind.COMPOUND`, checked after the whitespace-ambiguity
test and before the plain-simplex fallback.

**Consequence.** Hyphenated compounds resolve for free; only genuinely space-separated
multi-word headwords reach the model, which keeps the residue rate closer to the
expected 15%.

## D-12 (2026-09-02) — Migration placeholders: ambiguous residue as `compound`, migrated proper nouns as `entity_type=other`

**Context.** `migrate.from_v2` and `migrate.from_v13` must produce an immediately
schema-valid `Lexeme` — `kind` is required, and `Lexeme` validates that a `proper_noun`
block is present exactly when `kind == proper_noun`. Neither legacy shape typed `kind`
or `entity_type` at all, so migration cannot leave either unset or "unknown".

**Decision.** `_kind_for_migration` falls back to `LexemeKind.COMPOUND` whenever
`classify_kind_deterministic` returns `None` (a space-containing, genuinely ambiguous
headword). `_proper_noun_block` assigns `EntityType.OTHER` to every migrated proper
noun.

**Consequence.** Both values are placeholders, not classifications — a migrated
`compound` may really be a phrasal verb or idiom, and a migrated proper noun's real
entity type is unknown. Nothing on the entry itself distinguishes a placeholder `compound`
from one `classify_kind` actually resolved, so a store built by bulk migration needs a
full `classify_kind` retrofit sweep, not a filtered one, before `kind` can be trusted
(`DESIGN.md` §8, §5.6).

## D-13 (2026-09-02) — Legacy provenance stage `"variants"` is rewritten to `"renditions"`

**Context.** v2 provenance records use `stage="variants"`. v3 renamed that `StageName`
to `RENDITIONS`, and `Provenance.stage` is a `StageName` enum under a model that forbids
extra values — so a literal `"variants"` string in an old record would fail validation
the moment migration tried to load it.

**Decision.** `migrate._provenance` rewrites `stage: "variants"` to
`StageName.RENDITIONS.value` before validating a legacy provenance record. The rewrite
table (`_LEGACY_STAGES`) is a single-entry dict today, built to hold any future stage
rename the same way.

**Consequence.** A migrated entry's provenance history reads as if every affected record
were always generated by the `renditions` stage; the original `"variants"` label is not
separately preserved anywhere.

## D-14 (2026-09-02) — A legacy domain string equal to a taxonomy root maps to `<root>.general`

**Context.** `LEGACY_DOMAIN_MAP` was built for v1.3's common free-text domain values
("general academic", "history", ...), but some entries instead carry a
`domain:<root-name>` tag (e.g. `domain:language`, `domain:arts`) that names one of the
15 taxonomy roots directly. Falling through to `domain_hint` for these would be
needlessly conservative — the mapping is unambiguous.

**Decision.** `migrate._resolve_domain` checks `LEGACY_DOMAIN_MAP` first; failing that,
if the normalized text exactly equals one of `taxonomy.ROOTS`, it resolves to that
root's `.general` leaf.

**Consequence.** Only a genuinely unrecognized domain string falls through to
`domain_hint` for the `tag_domain` retrofit pass to resolve.

## D-15 (2026-09-02) — `geography`, `psychology`, and `economics` are best-fit legacy mappings

**Context.** `LEGACY_DOMAIN_MAP` must place every common v1.3 free-text domain
somewhere in the 15-root taxonomy. Three values have no obviously correct home: the
taxonomy has no dedicated geography or psychology root, and its `business` root is
commerce-oriented rather than a home for economics as a field of study.

**Decision.** `"geography"` maps to `nature.landforms`, `"psychology"` to
`people_society.general`, and `"economics"` to `business.general`. All three are
documented here as best-fit, not exact-fit.

**Consequence.** Entries migrated under these three legacy labels are more likely
candidates for the `tag_domain` retrofit pass to revisit than most. If the taxonomy
later grows a dedicated leaf for any of them, `LEGACY_DOMAIN_MAP` should be updated and
previously migrated entries re-tagged — a store should not be assumed to have picked up
a new leaf retroactively.

## D-16 (2026-09-02) — `spans.py` deliberately leaves three cases to the LLM fallback

**Context.** `find_span` is required to be a pure, model-free function (`docs/SCHEMA-
V3.md` §3), which means it needs a documented, bounded set of cases it does not attempt,
rather than an attempt at completeness that gets some cases subtly wrong — a wrong span
is worse than a missing one, because nothing downstream would know to distrust it.

**Decision.** Three cases are left to the LLM span-fallback stage on purpose:
irregular inflections not supplied via `forms` (`generate_forms` only covers regular,
rule-based patterns, so "run" → "ran" is never attempted); affix headwords with a
leading or trailing hyphen (a literal hyphen at the pattern's edge defeats a
word-boundary assertion); and any text where casefolding would change the string's
length (e.g. German `"ß"` → `"ss"`) — which is also why matching is done with
`re.IGNORECASE` directly against the original text rather than against a casefolded
copy with indices translated back.

**Consequence.** Examples in these three shapes are left unresolved by `find_span` and
collected by `unresolved()` for the batched (40-per-call) fallback stage, rather than
risking a deterministic but wrong offset.

## D-17 (2026-09-02) — `DraftOverview.domain` stays free text; the controlled tag comes from `DraftSense.domain`, so `generate` never runs `tag_domain`

**Context.** `contracts.DraftOverview.domain` is documented as staying free text
because "the overview call does not carry the taxonomy, and a hint costs a handful of
tokens" (`contracts.py:112-115`); it is stored as `Sense.domain_hint` and passed into
`build_senses_prompt(..., domain_hint=...)` (`prompts.py:285-307`,
`workflows/generate.py:145,162`). The binding tag is `DraftSense.domain: DomainTag`
(`contracts.py:162`), an enum field that structured output cannot put out of vocabulary.
`workflows/generate.py` never imports or calls the `tag_domain` stage or
`prompts.build_tag_domain_prompt` at all — the only caller of that prompt builder is
`workflows/retrofit.py::_tag_entry` (`retrofit.py:340-361`).

**Decision.** A freshly generated entry gets its per-sense domain from the `senses`
stage's enum-constrained field, at no extra cost and with no possibility of an
out-of-taxonomy tag. `tag_domain` exists solely as a `retrofit` pass, for senses whose
`domain` is `None` because they were migrated, generated before this contract, or left
unresolved by a failed `senses` call.

**Consequence.** `COST-MODEL.md`'s "full generate" total must not include `tag_domain`;
only `retrofit` pays for it, and only for the senses that actually need it
(`retrofit.py:324-328` sends only `sense.domain is None`).

## D-18 (2026-09-02) — `DraftKindVerdict` carries no `entity_type`; residue promotions get `entity_type=other`

**Context.** `contracts.DraftKindVerdict` has only `term` and `kind`
(`contracts.py:220-232`); its docstring gives the reason: the deterministic classifier
already catches capitalised proper nouns, so the residue reaching this batch is
"almost entirely multi-word forms," and asking every verdict for an entity type would
spend output tokens on a field that is null nearly every time. When the batch verdict
is `LexemeKind.PROPER_NOUN`, `retrofit._apply_kind` sets
`entry.proper_noun = entry.proper_noun or ProperNounInfo(entity_type=EntityType.OTHER)`
(`retrofit.py:194-213`) — the same placeholder pattern D-12 already uses for migration.

**Decision.** `classify_kind` batches never carry an entity type. A promotion to
`proper_noun` via this pass always lands as `entity_type=other`, to be refined later
(there is no separate refinement pass yet; a proper-noun entry only gets a real entity
type from a fresh `overview` call).

**Consequence.** A store retrofitted from `classify_kind` residue can have `proper_noun`
entries whose `entity_type` is uniformly `other` and is not distinguishable from one
that was actually judged `other` by the model — the same limitation D-12 already
documents for migration placeholders.

## D-19 (2026-09-02) — Structural contracts answer with list position, not id

**Context.** Four contracts ask the model to point at something the prompt already
listed, and every one of them answers with a 1-based (or 0-based) position in that list
rather than an id: `DraftSenseDomain.sense_ref` (`contracts.py:248`),
`DraftTargetResolution.target_ref` / `.sense_choice` (`contracts.py:267-268`), and
`DraftSpan.example_ref` (`contracts.py:283`). The docstrings give the same reason each
time — `sense_ref`'s: "one small integer is cheaper than repeating a part of speech and
an index per sense, and it cannot disagree with the list the model was shown"
(`contracts.py:244-246`). Every prompt builder that produces these lists numbers them
`  N. ...` from 1 (`prompts.py:344-390,393-410`), and every workflow consumer converts
back with `position = drafted.<ref> - 1` and bounds-checks it before touching the
underlying object (`workflows/retrofit.py:374-377`, `workflows/resolve.py:192-193`,
`workflows/generate.py:449-450`).

**Decision.** No structural contract answer is a `sense_id`, `lexeme_id`, or any other
derived identifier the model would have to reproduce verbatim; it is always a position
in a list the prompt itself rendered, resolved back to the real object by the workflow
after the call returns.

**Consequence.** A model answer can be syntactically valid but semantically stale (an
out-of-range position) if it hallucinates a number beyond what was listed; every
consumer treats an out-of-range ref as silently dropped rather than an error, per the
bounds checks cited above.

## D-20 (2026-09-02) — Examples enrichment rewrites only the first canonical example per target

**Context.** `workflows/enrich.py::_sense_work` builds the rendition source for the
`examples` field from `sense.examples.canonical()` — one `Example`, not the full list —
with the comment: "Only the first canonical example is rewritten per target. Rewriting
all of them would multiply output tokens by the example count for a set the reader sees
one of; the canonical set stays the place to add more examples." (`enrich.py:260-267`).

**Decision.** A `RenditionRequest(field=EXAMPLES, ...)` produces one rewritten sentence
per `(reading_level, register)` target, derived from the entry's single canonical
example, regardless of how many canonical examples exist.

**Consequence.** An entry with several canonical examples (legitimate under
`Renditions[Example]`'s uniqueness key, D-10) gets graded/register rewrites of only one
of them; a caller who wants every canonical example rewritten has no request shape for
that today.

## D-21 (2026-09-02) — `classify_kind` and the `spans` LLM fallback are idempotent via a provenance marker

**Context.** `domain` and `span` are nullable, so a retrofit sweep can tell "already
done" from the data itself; `kind` cannot, because `Lexeme.kind` is required. `retrofit._has_run(entry, stage)` checks whether any provenance record already carries
that `StageName` (`retrofit.py:131-133`), and `_classify_kind_pass` skips an entry that
already has a `CLASSIFY_KIND` record (`retrofit.py:228`). For entries the deterministic
rule decides (`classify_kind_deterministic`), `_marker(StageName.CLASSIFY_KIND)` writes
a zero-cost `Provenance` record with `model="rule:classify_kind_deterministic"`
(`DETERMINISTIC_MODEL`, `retrofit.py:73,120-128,237`) purely to mark the entry as
scanned, so a second sweep does not re-decide it. `_spans_pass` reuses the same
`_has_run(entry, StageName.SPANS)` check, but only to gate the *model* fallback
(`retrofit.py:427`); the free `find_span` pass runs over every example on every sweep
regardless, per the module docstring: "an example the model could not place is not
re-billed on every sweep; the free finder still runs over everything, every time."

**Decision.** Idempotence for `classify_kind` is a provenance marker (real or synthetic
zero-cost); idempotence for `spans` is the same marker checked only around the paid LLM
branch, never the free one.

**Consequence.** An entry whose `spans` model call previously failed for every example
in its residue is marked done anyway (the marker is written once residue exists and the
call is attempted, not once every example is placed) — see `_spans_pass`
(`retrofit.py:427-428`): the check gates a repeat *attempt*, not a repeat only when
unresolved examples remain. A store with genuinely unplaceable examples will not retry
them on a later sweep once a `SPANS` record exists.

## D-22 (2026-09-02) — `EnrichmentSpec.replace` is sections-only; rendition requests are always additive

**Context.** `EnrichmentSpec.replace` (`enrich.py:108`) is read in exactly three
places, all inside `_add_sections`: `entry.etymology is None or spec.replace`,
`entry.encyclopedia.canonical() is None or spec.replace`,
`entry.lexical_explanation.canonical() is None or spec.replace`
(`enrich.py:437,446,455-457`). `_add_renditions` and the `_plan`/`_sense_work`/
`_entry_work` functions it calls (`enrich.py:248-321`) never read `spec.replace`; a
rendition request is always computed as `renditions.missing(request.targets())` — a
pure diff against what already exists.

**Decision.** `replace` only ever forces regeneration of a long-form section's
canonical text (etymology, encyclopedia, lexical explanation); it has no effect on
gloss/example/encyclopedia/explanation *renditions*, which can only be added, never
overwritten, through `EnrichmentSpec`.

**Consequence.** There is no way to ask `enrich` to regenerate an existing rendition
(say, a stale `grade_1`/`plain` gloss) short of deleting it from the entry first; the
only "replace" lever touches canonical section text.

## D-23 (2026-09-02) — D-7 extended: sequential (non-`gather`) call sites also re-raise `BudgetExceededError` explicitly

**Context.** D-7 fixed `asyncio.gather(return_exceptions=True)` swallowing
`BudgetExceededError` in `generate` and `enrich`. `resolve.py::_resolve_chunk`
(`resolve.py:172-186`) and `retrofit.py`'s three call sites
(`_classify_kind_batch:272-284`, `_tag_entry:355-367`, `_span_fallback:465-479`) are not
gathered — each awaits one `runner.run(...)` per chunk/entry, sequentially — so a raised
`BudgetExceededError` would propagate on its own even without an explicit handler,
since `errors.BudgetExceededError` is not a subclass of `GenerationError`
(`errors.py:23,42`) and so is not caught by the `except GenerationError` clause that
follows it in every one of these sites.

**Decision.** Every one of these sites still writes `except BudgetExceededError: raise`
before `except GenerationError as exc: ...`, even though the sequential control flow
does not strictly require it, so the rule from D-7 — a budget stop is a run-level
condition, never absorbed into a per-item failure — is stated the same way at every
model-calling site in the codebase, not only where `gather` makes it load-bearing.

**Consequence.** A future refactor that broadens the `except GenerationError` clause
(or replaces it with `except Exception`) at any of these sites has an explicit,
adjacent `BudgetExceededError` guard to preserve rather than a control-flow accident to
rediscover.

## D-24 (2026-09-02) — `attach_long_form` is shared between `generate` and `enrich`

**Context.** `workflows/generate.py::attach_long_form(entry, output, provenance_id)`
(`generate.py:549-587`) is the single place that turns a `DraftEtymology`,
`DraftEncyclopedia`, or `DraftLexicalExplanation` into the corresponding field on
`Lexeme`, including `_replace_canonical`, which sets or overwrites the `(neutral,
plain)` rendition of `entry.encyclopedia` / `entry.lexical_explanation`
(`generate.py:580-587`). `workflows/enrich.py` imports it directly —
`from opengloss_generator.workflows.generate import attach_long_form`
(`enrich.py:53`) — and calls it from `_add_sections` (`enrich.py:495`) instead of
reimplementing section-attachment.

**Decision.** There is exactly one function that decides what "the encyclopedia
section" (or etymology, or lexical explanation) means as stored content; `generate` and
`enrich` both call it rather than each maintaining its own notion of the canonical
rendition for these three fields.

**Consequence.** A future change to how a long-form section is attached (e.g. a new
field on `Etymology`) is made once, in `generate.py`, and both workflows pick it up;
`enrich.py` has a one-directional dependency on `generate.py` for this reason.

## D-25 (2026-09-02) — `PROMPT_VERSION` bumped to `"2"`

**Context.** `prompts.PROMPT_VERSION = "2"` (`prompts.py:61`); every stage call site in
`generate.py`, `enrich.py`, `resolve.py`, and `retrofit.py` passes
`prompt_version=prompts.PROMPT_VERSION` explicitly into `runner.run(...)`, and
`StageRunner.run`'s own parameter default is `prompt_version: str = "1"`
(`stages.py:94`) — a fallback for a caller that forgets to pass one, not a value any
current call site actually uses. `schema.Provenance.prompt_version` is a required
`str` field with no default (`schema.py:205`), and `migrate._provenance` preserves
whatever `prompt_version` a legacy record already carries (`migrate.py:316-326`) rather
than rewriting it — unlike `stage`, which D-13 does rewrite.

**Decision.** The v3 instruction rewrite (typed relations replacing six lists,
confusables, the taxonomy block, kind/domain moving into `overview`/`senses`, the four
new structural stages) is a new prompt version, not a continuation of whatever version
number the pre-v3 prompt text carried.

**Consequence.** A provenance record's `prompt_version` alone distinguishes v3-era
content from anything recorded under the old instruction text — including, after a
`migrate` pass, legacy provenance sitting in the same entry's provenance table as fresh
v3 records — without needing to inspect instruction text or guess from `generated_at`.
Bumping instruction text again without bumping this constant would silently defeat that
distinction.

## D-26 (2026-09-02) — A lowercase headword's kind is read from its own prose, ignoring sentence-initial capitals

**Context.** Rule 4 of `classify_kind_deterministic` — "a leading capital is a proper
noun" — assumes the headword is stored as written. The v1.3 working store at
`/nas4/data/workspace/curriculum/data/lexicon` does not: `word` is lowercased for every
entry. So `einstein.json` and `london.json` reach rule 8 and are classified `simplex`
with full confidence. Worse, a confident deterministic answer never enters the residue,
so the `classify_kind` retrofit pass could not fix it either: all ~206K entries of that
store would migrate with their proper nouns permanently typed `simplex`. The signal is
present, just not in the headword — the entry's own glosses, encyclopedia section and
lexical explanation write "London" and "Einstein" with a capital, over and over.

**Decision.** `classify_kind_deterministic(headword, *, evidence=None)` takes the entry's
own text. For a lowercase headword it counts capitalised and lowercase whole-word
mentions of the headword in that text and, when there are at least two capitalised
mentions and they outnumber the lowercase ones, returns `proper_noun`; when there is at
least one capitalised mention but no such majority, it returns `None` — the residue, for
the batched classifier — rather than asserting `simplex`; with no capitalised mention at
all it falls through to the existing rules. The test runs before the whitespace-ambiguity
rule, so "new york" gets it too. `from_v13` and `from_v2` pass their own payload text;
the retrofit pass passes `migrate.entry_evidence(entry)`.

**Sentence-initial mentions are excluded from both counts**, where "sentence-initial"
means the only characters between the mention and the start of the text, a newline, or a
`.`/`!`/`?` are whitespace or markdown decoration (`*`, `#`, `>`, bullets, opening
quotes and brackets). A capital in that position is forced by orthography and says
nothing about the word: every one of these entries opens with `**Abseil** is …` or
`**Alluding** is …`, and counting those would promote every headword in the store to a
proper noun. Excluding them is what makes the rule discriminate — on the real files the
counts are einstein 7:6, london 12:0, abseil 0:11, alluding 0:7, mother-in-law 0:6.
Lowercase mentions are excluded in the same position for the same reason: the rule reads
only positions where the writer had a free choice.

**Consequence.** Proper nouns in a lowercased store are recovered for free, at migration
time and again on the first `classify_kind` sweep (migration writes no `classify_kind`
marker, so D-21's idempotence guard does not hide an already-migrated entry from the
correction). The threshold of two, and the majority test, are deliberately strict: a
single capitalised mention is as likely to be an eponym ("Named after Albert Einstein")
as a name, so that case buys a model call instead of a guess. Because a genuinely
undecided single-word headword now returns `None`, `_kind_for_migration` no longer uses
D-12's `compound` placeholder for it — `compound` remains the placeholder only for
space- or hyphen-containing forms, and a bare word keeps `simplex`. The rule reads the
entry's own generated prose, so a systematically miscapitalised source would mislead it;
nothing cross-checks against an external gazetteer. Related: the residue prompt now
carries one ≤120-character gloss snippet per term (~30 input tokens), because the surface
form alone cannot tell the classifier whether "einstein" is the physicist or the unit of
radiant energy.

## D-27 (2026-09-02) — `Register` realigned to TBX DC-423; `professional` renamed to `formal`

**Context.** `docs/REGISTERS.md` researched every citable register/style taxonomy
(ISO 12620 / TBX Master Data Category List DC-423, TBX-Basic v4, TEI `<usg>`, MARC
008/22, Oxford Languages, Wiktionary, Joos, Biber & Conrad) against the pre-D-27
`Register` enum (`plain, informal, technical, professional, marketing`) and found it
conflated three unrelated axes: formality (Joos), a TEI/OED/dictionary-practice
register vocabulary, and Biber & Conrad's situational *genre* categories. Of the six
sources surveyed, none uses "professional" as a register label, while "formal" is used
by every one of them; DC-423's live picklist (`colloquialRegister`, `neutralRegister`,
`technicalRegister`, `in-houseRegister`, `bench-levelRegister`, `slangRegister`,
`vulgarRegister`) also offered two registers — `slang` and `in-house` — that the old
enum had no room for. § 7a of that research recommended a full two-enum split
(`Formality` / `Genre`); § 7c gave the smaller fallback actually adopted here: keep one
enum, align its formality members to DC-423, and stop pretending `marketing` is a
formality level.

**Decision.** `Register` becomes `plain, informal, formal, technical, slang, in_house,
marketing`:

- `plain` keeps its value (not renamed to `neutral`) and is documented as DC-423
  `neutralRegister` — `ReadingLevel` already has a `NEUTRAL` member, and a
  `(neutral, neutral)` key reads badly.
- `informal` = DC-423 `colloquialRegister`, `technical` = DC-423 `technicalRegister`,
  unchanged in meaning.
- `formal` replaces `professional` (DC-423 has no `professional`; "formal" is the
  dictionary-practice label every surveyed source uses).
- `slang` (DC-423 `slangRegister`) and `in_house` (DC-423 `in-houseRegister`) are added
  as available-but-not-default registers.
- `marketing` is kept on this axis rather than split into a third enum, but is now
  documented in the class docstring as a Biber & Conrad *genre* value, and
  `Register.is_genre` returns `True` only for it.
- `TBX_REGISTER_MAP: dict[Register, str | None]` records the DC-423 picklist value per
  member (`None` for `formal` and `marketing`, which have no DC-423 analogue).
- `FORMALITY_ORDER = (slang, informal, plain, formal)` gives the formality scale.
  `technical`, `in_house`, and `marketing` are deliberately excluded: technical register
  is orthogonal to formality rather than a point on it, `in_house` is jargon rather than
  a formality level, and `marketing` is a genre value, not formality at all — so a
  single five- or six-member ordered scale would misrepresent all three.
- `Register._missing_` maps the retired string `"professional"` to `FORMAL`, so stored
  data and `migrate.py`'s v2 `_v2_variant` (which already calls
  `Register(raw.get("register") or raw["style"])`) keep loading old payloads without
  extra migration code.
- `DEFAULT_REGISTERS` becomes `(informal, formal, technical, marketing)` — same four
  slots as before, `formal` replacing `professional`; `slang` and `in_house` are
  available via `--registers` but are not defaults.
- `prompts.RENDITIONS_INSTRUCTIONS` replaces the `professional` contrastive example with
  `formal` and adds `slang`/`in_house` examples in the same contrastive-pair style;
  `PROMPT_VERSION` bumps to `"5"`.

**Consequence.** Every stored rendition keyed `"register": "professional"` still loads,
now as `Register.FORMAL`, and re-serialises as `"formal"` — a one-way normalisation on
write, verified by a schema-level round-trip test. Callers that hardcoded
`Register.PROFESSIONAL` (`audit.py`'s pristine gloss targets, `config.py`'s
`DEFAULT_REGISTERS`, and the corresponding tests) are updated to `Register.FORMAL`;
`cli.py`'s `_parse_registers` and `migrate.py`'s v2 conversion path needed no change,
since both already parse through `Register(...)` and inherit `_missing_` for free. The
two-enum split from § 7a of `docs/REGISTERS.md` (`Formality` / `Genre`) remains
unimplemented — this decision is the documented smaller fallback, not the full
recommendation, and a future iteration can still split `marketing` out onto its own axis
without another rename, since `Register.is_genre` already marks the one value that would
move.

## D-28 (2026-09-02) — `Lexeme.zipf` and its two provenance fields, van Heuven scaling

**Context.** `docs/STANDARDS-PLAN.md` § 2 (A3) calls the stored `frequency` field a raw
count that is not comparable across corpora and not what a curriculum designer reasons
in. The fix adopted there is the Zipf scale (van Heuven, Mandera, Keuleers & Brysbaert,
2014): a log frequency per billion words, corpus-size-independent and running roughly
1-7 for real vocabulary. Computing it honestly requires knowing which corpus `frequency`
was drawn from and how large that corpus was; `/nas4/data/workspace/curriculum/src/
curriculum/pipelines/wiki_frequency.py` confirms the v1.3 source was
`load_dataset("wikimedia/wikipedia", "20231101.en")`, but that pipeline never recorded
the corpus's total token count, so the size half of the pair cannot be recovered from
migration alone.

**Decision.** `Lexeme` gains three fields: `zipf: float | None`, `frequency_corpus: str
| None` (e.g. `"wikimedia/wikipedia:20231101.en"`), and `frequency_corpus_tokens: int |
None`. A new dependency-free module, `frequency.py`, exposes `zipf_scale(count,
corpus_tokens) -> float` implementing `log10((count + 1) / corpus_tokens * 1e9)` — the
`+ 1` is Laplace smoothing so `count = 0` does not raise a domain error — with the 1-3
(low) / ~3 (about once per million) / 4-7 (high) interpretation bands documented on the
function. `Lexeme.compute_zipf()` fills `zipf` only when both `frequency` and
`frequency_corpus_tokens` are set; it is not a validator, since most entries will not
have `frequency_corpus_tokens` for a while yet and a required-but-usually-absent
computed field is worse than an explicit method callers run when they have the inputs.
`migrate.from_v13` sets `frequency_corpus` to the confirmed v1.3 source string
unconditionally (whether or not `wiki_frequency` itself was present) but leaves
`frequency_corpus_tokens` `None`, with a comment pointing at this gap so it reads as
known-missing rather than forgotten.

**Consequence.** Every field defaults to `None`, so no existing payload or test breaks.
`zipf` stays unpopulated across the whole migrated store until a separate task recovers
or recomputes `wikimedia/wikipedia:20231101.en`'s total token count — tracked as its own
follow-up, not solved here. Once that number exists, a one-time pass can call
`compute_zipf()` over the store; nothing about today's change needs to be revisited to
do that, since the corpus name is already attached to every migrated entry.

## D-29 (2026-09-02) — `Sense.concept_id` is validated against the ILI/project-concept format now, ahead of population

**Context.** `docs/STANDARDS-PLAN.md` § 2 (A5) reserves `concept_id` for a future
alignment to the Global WordNet Association's Interlingual Index (ILI), so that a sense
can eventually point at a language-independent concept shared across wordnets, while
still allowing project-specific concepts that have no ILI counterpart. Reserving the
shape now, before any value is ever written, means a later population pass cannot
silently write something the format rejects, and every current caller that leaves
`concept_id` as `None` is unaffected.

**Decision.** `Sense.concept_id` accepts exactly two shapes, checked by a
`field_validator`: `^ili:i[1-9][0-9]*$` (a real ILI id, e.g. `ili:i35545` — no leading
zero, since ILI ids do not have one) or `^og:c-[0-9a-f]{16}$` (a project concept, 16
lowercase hex digits). Anything else, including the old bare-string convention, raises a
`ValueError` naming both accepted shapes. A new helper, `project_concept_id
(member_sense_ids: Iterable[str]) -> str`, derives the project-concept id
deterministically (D-1: identifiers are derived, never random) as `"og:c-"` plus the
first 16 hex characters of the blake2b digest of the member sense ids, sorted and
comma-joined before hashing — sorting first means the id does not depend on the order a
caller happens to assemble a synset's members in.

**Consequence.** No `concept_id` is populated by this change; every existing entry keeps
`concept_id = None` and validates unchanged. A future ILI-alignment or
synset-construction pass has a format to target and a deterministic id function to call,
rather than inventing both under time pressure once real WordNet alignment data is in
hand.

## D-30 (2026-09-02) — Proper nouns are exempt from the hygiene pass's headword-initial gloss rewrite

**Context.** `docs/CORE-DIARY.md`'s Iteration 2 mid-retrofit observations record the
defect directly: over the first 500 hygiene-swept entries, common-noun rewrites came out
right ("Games are organized activities…"), but proper nouns gamed the check — "Congo
River, a major central African river…" became "The Congo River is a major…", satisfying
the no-headword-initial-gloss rule by construction rather than by writing a better
definition. A proper noun's definition legitimately names its own entity (WordNet does
the same for e.g. place and person entries), so the honest fix is to stop asking the
model to rewrite these at all, not to tighten the prompt further.

**Decision.** In `workflows/retrofit.py`'s hygiene pass, step (c) — the one nano call
that rewrites headword-initial glosses — now skips every entry whose `kind` is
`LexemeKind.PROPER_NOUN` entirely: such an entry contributes no offenders, so it costs
nothing and receives no rewrite call, while steps (a), (b), and (d) still run over it
unchanged. `audit.py`'s `gloss_starts_with_headword` consistency check gets the matching
one-line exemption, so a proper noun's headword-initial gloss (correct, per the above)
is not counted as a defect either.

**Consequence.** A proper-noun entry with a headword-initial gloss is now left alone by
both the rewrite pass and the audit metric; the superseded text from any earlier,
incorrect rewrite already made under the old behavior remains recoverable from
`Provenance.note`, so nothing already written is lost by this change — it only stops the
behavior going forward. `docs/CORE-DIARY.md` iteration 2 already queued this fix for
"iteration 3"; this decision is that iteration.

## D-31 (2026-09-02) — Retrofit and resolve sweeps run through the worker pool, holding each entry's lock across its model call

**Context.** `docs/CORE-DIARY.md` Iteration 2, finding 4: each of `retrofit.py`'s four
passes was a `for lexeme_id in ids:` loop awaiting one model call at a time, so the
configured worker count never applied — hygiene advanced ~40 entries/min against a
64-worker configuration, and `tag_domain`, which needs a call for nearly every entry once
hygiene has cleared the weak domain tags, would have taken on the order of a day over 10K
entries. Reading the same code turned up a second, quieter defect: three of the four
passes (`hygiene`, `tag_domain`, `spans`) read the entry *outside* the lock and wrote it
*inside* — a lost-update hazard the moment two workers, or two retrofit processes over one
store, touch the same entry. Only `classify_kind`'s model branch read and wrote under one
hold. `resolve_store` had the throughput half of the same problem, though its locking was
already correct.

**Decision.** Every pass now builds its id list and drives it through
`runner.run_pool(ids, handler, workers=..., stop_event=...)`, where the handler is

```python
async with store.locked(lexeme_id):
    entry = store.read(lexeme_id)
    ...                      # deterministic work, then the model call if one is due
    store.write(entry)
```

The lock is deliberately held across the model call. Per-entry contention is nil — an id
is queued once per pass, and the passes run one after another — so the hold costs only a
lock file that lives for the duration of one call, and it buys the invariant that no entry
is ever read outside the lock it is written under. The one exception is `classify_kind`'s
residue batch, which decides 50 entries per call and therefore cannot hold 50 locks: it
re-reads each entry under that entry's own lock and applies the verdict there, so the write
is still read-modify-write inside one lock. The residue is sorted before batching, so the
same store yields the same batches — and the same prompts, and the same cache keys —
whatever order the workers finished in. `resolve_store` gets the same pool treatment; its
lock discipline was already right.

Counters are no longer bare `+=` on shared ints. Each pass owns a `_Tally` whose every
mutation happens inside an `asyncio.Lock`, and `resolve_store` merges its per-entry
outcomes under one. Single-threaded asyncio does make `+=` atomic across an await-free
statement, but that is a property of the interpreter rather than of this code, and it stops
being true the first time a handler grows an `await` between reading a counter and writing
it back; the lock makes the discipline explicit and testable, and a test asserts it holds.

A budget stop is reported, not raised. `run_pool` already converts `BudgetExceededError`
into a clean stop of the whole pool, so the pass records `stopped_reason="budget"`,
`run_retrofit` skips the remaining passes and *returns* the outcome it has;
`ResolveOutcome` gained the same field. `run_retrofit` also takes `workers` (defaulting to
`runner.config.concurrency.workers`) and a `stop_event`, so a caller can share its session's
event. Long passes log `retrofit_pass_progress` every 500 entries — entries done, entries
and items changed, calls, cost so far — so a run that takes hours is legible from the log
file rather than from a `du` over the store.

**Consequence.** The idempotence markers are untouched, so a job killed mid-pass still
resumes without re-billing, and a retrofit already running under the old code can be
stopped and relaunched under the new code with no migration of any kind. Throughput becomes
a configuration question rather than a code question: `tag_domain` over 10K entries is now
bounded by the provider's rate limits and the configured worker count, not by one call at a
time. One follow-up is left open deliberately, in a file this change does not own:
`cli.py`'s `retrofit` and `resolve --all` commands should map the returned
`stopped_reason` onto `session.stop_reason`, or a budget-stopped run will report
`stop_reason="completed"` in its summary — previously the exception propagating out of the
workflow set that for them.

## D-32 (2026-09-02) — `PartOfSpeech` gets a UPOS/LexInfo export crosswalk and an `upos_for` proper-noun rule

**Context.** `docs/STANDARDS-PLAN.md` § 2 (A1) and the sourced survey in `STANDARDS.md`
§ 1 compare our ten-value `PartOfSpeech` enum against Universal Dependencies' UPOS v2
tagset and LexInfo 3.0's `PartOfSpeech` individuals. STANDARDS.md § 1d's own
recommendation is to store a mapping only and leave the enum as-is: UD's tagset
disambiguates a *token in a treebank*, where surrounding context tells a coordinating
conjunction from a subordinating one; a *dictionary sense* carries no such context, and
our schema already handles "for" being both a preposition and a conjunction via separate
`POSEntry` entries rather than a finer POS value. Two crosswalk cells are lossy in our
direction: `conjunction` is ambiguous between UD's `CCONJ`/`SCONJ`, and proper nouns have
no stored POS distinct from `noun` — UD tags every proper noun `PROPN` regardless.

**Decision.** `schema.py` gains `UPOS_MAP: dict[PartOfSpeech, str]` and
`LEXINFO_MAP: dict[PartOfSpeech, str]`, both total over every `PartOfSpeech` member, plus
a `PartOfSpeech.upos` property returning `UPOS_MAP[self]`. `CONJUNCTION` maps to `CCONJ`
(the more common case) with the SCONJ collapse recorded in the map's docstring, not
silently picked. No `auxiliary`/`particle` members are added — per STANDARDS.md § 1c/1d
they never occur as dictionary headwords in our data, so there is nothing for them to
tag. `PROPN` is not added as a stored value either: a new function `upos_for(entry:
Lexeme, pos: PartOfSpeech) -> str` applies UD's proper-noun rule at export time
(`entry.kind is PROPER_NOUN and pos is NOUN` → `"PROPN"`, else `pos.upos`), so the
distinction `LexemeKind.PROPER_NOUN`/`ProperNounInfo` already carries is not duplicated
into a second stored field.

**Consequence.** No schema migration: the enum's stored values are unchanged, so every
existing `POSEntry` still validates. `UPOS_MAP`/`LEXINFO_MAP`/`upos_for` are additive
and read-only — nothing in `generate.py`, `enrich.py`, or `retrofit.py` calls them yet;
they exist for a future CoNLL-U-adjacent or LexInfo RDF export
(`docs/STANDARDS-PLAN.md` § 5's `opengloss-graph` exporter) to consume.

## D-33 (2026-09-02) — `EtymologySegment.language_code`, schema field only, no population

**Context.** `docs/STANDARDS-PLAN.md` § 2 (A2) and `STANDARDS.md` § 3 note that
`EtymologySegment.language` is unconstrained free text over a small, enumerable universe
of roughly 25 recurring display names, and that ISO 639-3 has a dedicated code for every
one of them except two reconstructed proto-languages, Proto-Germanic and
Proto-Indo-European, which have no ISO 639-3 code at all (`gem` is the ISO 639-3
*family* code "Germanic languages," never to be read as a code for the reconstructed
proto-language). STANDARDS.md § 3b's recommendation is Wiktionary's own etymology
sentinels, `gem-pro` and `ine-pro`, as the de facto non-ISO extension every etymological
resource that hits this same gap uses. This task adds the schema field only: A2's
deterministic table plus the nano-call residue pass over the etymology-code histogram
(`etymology_codes.py`, the `hygiene` pass's new step (e)) is explicitly deferred, so no
segment's `language_code` is populated by this change.

**Decision.** `EtymologySegment` gains `language_code: str | None = None`, validated by
a `field_validator` against `^[a-z]{3}$` (a lowercase ISO 639-3 shape) or membership in
`RECONSTRUCTED_LANGUAGE_CODES = frozenset({"ine-pro", "gem-pro"})` — exactly the two
exceptions STANDARDS.md § 3a names, no more. The validator checks *shape*, not whether a
code is the semantically correct one: `"gem"` (the family code) passes the ISO 639-3
shape check the same as `"lat"` does, since distinguishing "a valid 639-3 code" from "the
right 639-3 code for this segment" is exactly the judgement A2's deferred population pass
exists to make, not something a format validator can settle. `language` is untouched and
stays the display name.

**Consequence.** Every existing `EtymologySegment` — both call sites that construct one,
`workflows/generate.py` and `migrate.py` — keeps validating unchanged, since the new
field defaults to `None`. No segment anywhere in the store gains a `language_code` from
this change; A2's own deterministic table, alias handling, and nano-residue pass are a
separate follow-up task with its own cost case, tracked as deferred rather than
implemented here.

## D-34 (2026-09-02) — `ReadingLevel` gets a documented crosswalk; `readability.grade_band` now reads its bands from `schema.FK_BANDS`

**Context.** `docs/STANDARDS-PLAN.md` § 2 (A6) asks for two things: a documented
crosswalk from our four-plus-neutral `ReadingLevel` enum to CCSS text-complexity bands,
the Lexile Framework, CEFR, MARC 008/22, and approximate reader age (`STANDARDS.md` § 6c,
reusing `docs/REGISTERS.md` § 7c's CEFR/MARC columns verbatim, as STANDARDS.md itself
does); and a refactor so `readability.grade_band`'s acceptance bands and that
documentation cannot silently disagree. STANDARDS.md § 6d's own verdict is that none of
the four external scales can replace `ReadingLevel` as a stored value — they measure an
*already-written* text, while ours is a *generation-time target* — so this is export/
reference documentation, not a schema-value change.

**Decision.** `schema.py` gains a frozen dataclass `LevelCrosswalk` (`ccss_band,
lexile_band, cefr, marc_audience, approx_age`, all `str`) — a dataclass, not a pydantic
model, since nothing here is ever parsed from or serialised to a stored entry — and
`READING_LEVEL_CROSSWALK: dict[ReadingLevel, LevelCrosswalk]`, total over every member,
plus a `ReadingLevel.crosswalk` property. `grade_1`'s row is explicitly documented as an
extrapolation below the CCSS/Lexile quantitative floor (STANDARDS.md § 6c/6d), not
presented as a sourced figure. Next to that table, `schema.py` also gains
`FK_BANDS: dict[ReadingLevel, tuple[float, float]]`, and `readability.grade_band` is
rewritten to `return FK_BANDS[level]` instead of keeping its own `_BANDS` dict keyed by
string value. The band numbers themselves are **unchanged** from their pre-existing
values (`grade_1` ≤ 3.0, `grade_5` 3.0–7.0, `grade_10` 7.0–12.0, `college` ≥ 10.0,
`neutral` unbounded): STANDARDS.md § 6a's own CCSS Flesch-Kincaid column has no K-1 row
and uses narrower, *scored-text* bands (e.g. 4.51–7.73 for 4th-5th) that are not a
sourced correction to a *generation-time-target-with-one-retry* band — nothing in
`STANDARDS.md` gives a reason tied to a defect in the existing numbers, so they are
carried over as-is, only relocated to sit beside the crosswalk they document alongside.
`readability.py`'s module docstring, which previously advertised "no dependencies," is
updated to describe this: it now imports `FK_BANDS`/`ReadingLevel` from `schema.py` at
module level (no import cycle exists in that direction) so the acceptance check and the
documented crosswalk are read from one place.

**Consequence.** `test_readability.py` and the new `test_schema.py` cases both assert
`grade_band(level) == FK_BANDS[level]` for every level — true by construction now, which
is the point: a future change to one cannot happen without changing the other, since
there is only one table. No stored data changes; `Assessment.readability_grade` and the
retry logic in `workflows/enrich.py` are unaffected, since `grade_band`'s return value is
identical to what it was before this change.

## D-35 (2026-09-02) — `RelationType.namespace` and `EntityType`'s OntoNotes/Schema.org maps: export crosswalks only, no enum rename or retrofit

**Context.** `docs/STANDARDS-PLAN.md` § 8 reconciles its own earlier § 3 proposals
(meronym/holonym subtyping and a `see_also`/`instance_of` rename for `RelationType`;
adopting the OntoNotes 18-type scheme wholesale for `EntityType`) against the sourced
research in `STANDARDS.md` §§ 2 and 4, and both come back smaller: `RelationType`
"mostly aligns with GWA/WN-LMF... namespace property + export map only, no enum rename,
no retrofit," and `EntityType` "keep our 8 values; add `ONTONOTES_MAP` and
`SCHEMA_ORG_MAP` for export" since OntoNotes' full NER tagset is a general-purpose
tagset and ours is deliberately narrower — a proper-noun-only field with no use for
`DATE`/`MONEY`/`PERCENT` and the other numeric/temporal types that can never apply to a
proper noun. Both items are grouped into one decision because both land in the same
shape: an enum's stored values are untouched, and a same-scope export-only crosswalk is
added beside it.

**Decision.** `RelationType` gains a `namespace` property returning `"wn"` for every
member the Global WordNet Association / WN-LMF relation inventory already covers, or
`"og"` for the three with no standards home at all — `confusable_with`, `used_with`,
`collocation` (`STANDARDS.md` § 2c: no WN-LMF or SKOS analogue exists for any of the
three). `WN_RELATION_MAP: dict[RelationType, str]` gives the exact WN-LMF `relType`
string for every `"wn"`-namespaced member — `synonym` maps to `eq_synonym` (WN-LMF has
no bare `synonym`, since within-synset terms are synonymous by construction),
`meronym`/`holonym` map to WN-LMF's own unspecified-subtype catch-alls rather than the
finer `mero_part`/`holo_member`/etc. split (that split is the deferred v4 item § 8 names,
not this one), and `instance_of` keeps its name here despite WN-LMF calling the same
concept `instance_hypernym`. A smaller, deliberately partial `SKOS_RELATION_MAP` records
a looser SKOS reading for a handful of `"wn"`-namespaced members (`hypernym` →
`skos:broader`, etc.) — supplementary, not a second namespace any member is exclusively
in. `EntityType` gains `ONTONOTES_MAP: dict[EntityType, str | None]` (`None` for
`SPECIES` and `OTHER`, an explicit documented gap rather than a force-fit — `STANDARDS.md`
§ 4c: none of OntoNotes' 18 types cover taxonomic names) and
`SCHEMA_ORG_MAP: dict[EntityType, str]` (total; `SPECIES` → `Taxon`, the dedicated
Schema.org type that has no OntoNotes counterpart). `PLACE` stays a documented lossy
mapping to `GPE` (OntoNotes' common case), not `LOC`, since nothing in the generation
pipeline currently disambiguates the two.

**Consequence.** No enum values are renamed, no legacy `_missing_` alias is needed, and
no retrofit pass runs: every stored `Relation.type` and `ProperNounInfo.entity_type`
keeps its current value and meaning. The four new maps are read-only, additive lookup
tables for a future `opengloss-graph` RDF/WN-LMF export; nothing in this codebase calls
them yet.

## D-36 (2026-09-02) — `QAFlag`, a closed MQM-grounded enum for `Assessment.qa_flags`; and the LCC/IPTC domain-root crosswalk

**Context.** `docs/STANDARDS-PLAN.md` § 3 (B3) and § 4 (C1) are grouped into one decision
because both close out the plan with the same shape: a new closed vocabulary with no
retrofit of existing data. B3: `Assessment.qa_flags` was `list[str]`, free text that
cannot be aggregated or used to target a re-generation pass ("show every entry with a
factual-error flag"); `STANDARDS.md` § 9's own recommendation is MQM Core, "the only
widely-cited, actively maintained... closed typology for this class of problem," licensed
CC BY 4.0 and reinterpreted in § 9b for dictionary-generation QA rather than translation
QA. C1: `taxonomy.py`'s 15 domain roots have no adopted external equivalent — `STANDARDS.md`
§ 5d confirms LCC (a shelving taxonomy) and IPTC Media Topics (a news-event taxonomy)
both answer a different organizing question than "what life/subject domain for a K-12
dictionary sense," and several roots are legitimately one-to-many against both — but an
export-time crosswalk table is still useful for anyone who wants to filter or export by
either standard.

**Decision.** `schema.py` gains `QAFlag(StrEnum)`: sixteen MQM Core-grounded members
carrying STANDARDS.md § 9c's recommended list verbatim (`factual_error` through `other`,
each commented with its MQM Core parent dimension/type), plus four project-only members
with no MQM analogue at all — `og.headword_initial`, `og.artifact_relation`,
`og.readability_miss`, `og.duplicate_gloss` (`docs/STANDARDS-PLAN.md` § 3's own list).
`Assessment.qa_flags` changes from `list[str]` to `list[QAFlag]`; because `QAFlag` is a
plain `StrEnum` with no `_missing_` hook, an unrecognised string now raises a
`ValidationError` on load rather than being silently accepted, per the project's existing
closed-vocabulary preference (the same rationale `taxonomy.py` gives for replacing v1.x's
free-text `domain`). `Assessment.flag(flag: QAFlag) -> None` is added as the one
idempotent way to add a flag in code (`if flag not in self.qa_flags: self.qa_flags.append(flag)`),
rather than a lenient validator that maps unknown strings onto a catch-all — an unknown
string is a bug to surface, not data to coerce. `workflows/enrich.py`'s readability-miss
path is the first writer: `_Measured` gains a `missed_band` flag set from the *final*,
post-retry state, and `_apply_renditions` calls `assessment.flag(QAFlag.OG_READABILITY_MISS)`
exactly when a rendition still misses its band after any retry — a rendition a retry
fixed does not carry the flag forward. `audit.py` gets a matching read-only counter,
`renditions_with_readability_miss_flag`, tallied in `_bump_field_coverage`: it reports how
much of that flag `enrich.py` has populated without `audit.py` ever writing one itself,
consistent with the module's "measures, never mutates" contract. `taxonomy.py` gains
`LCC_MAP: dict[str, tuple[str, ...]]` and `IPTC_MAP: dict[str, tuple[str, ...]]`, both
total over `ROOTS`, with letters/codes taken from `STANDARDS.md` § 5a/5b/5c; one-to-many
rows are commented inline, and `IPTC_MAP["history"]` is an explicit empty tuple — IPTC
has no top-level topic for history as a subject at all, a documented gap rather than a
forced fit or a missing key.

**Consequence.** Any stored `Assessment.qa_flags` value that is not one of the twenty
`QAFlag` members now fails validation on load; a grep of the codebase before this change
found no such value in code, but a store built from real generation runs before this
task should be checked before this schema ships against it (`migrate.py` gets no
automatic free-text-to-`QAFlag` mapping pass here — that migration, if needed, is a
follow-up, since we have no visibility into what free-text values, if any, a live store
already carries, per `STANDARDS.md` § 9e). `LCC_MAP`/`IPTC_MAP` are read-only and
additive; nothing in the generation or retrofit pipeline consults them.

## D-37 (2026-09-02) — `repair` retrofit pass: retire exact-duplicate senses, fill in missing canonical examples

**Context.** `docs/CORE-DIARY.md`'s post-retrofit-3 audit found 3 entries with a duplicate
canonical gloss and 12 with zero examples on any sense — small counts, but the kind of
defect that should never need a human to notice it entry by entry. Iteration 3's
"findings to address next" put both on the list, in order, ahead of anything else queued
for iteration 4: a duplicate sense inflates a polysemy count and gives the resolver two
identical targets to choose between with no way to prefer one; a sense with no example at
all has nothing for a reading-level rendition sweep to rewrite.

**Decision.** `workflows/retrofit.py` gains a fifth pass, `repair`, appended to
`RetrofitPass.ALL` after `spans` so its duplicate check sees every other pass's writes
first. Two steps, free first: (a) within an entry, `_retire_duplicate_senses` walks
`iter_senses()` — parts of speech in document order, senses by index within each — and
marks `retired=True` on the later of any two non-retired senses whose canonical gloss is
identical once case, whitespace, and a single trailing period are normalised away
(`_normalized_gloss`); nothing is ever deleted or renumbered (D-1), and a sense already
retired is skipped, so the step is naturally idempotent with no marker. (b) One nano call
per entry, covering every non-retired sense the first step left with zero canonical
examples: the model is shown those senses' glosses, numbered, plus every other sense's
gloss as unnumbered context so it can tell them apart, and instructed to write one or two
natural sentences per numbered sense — the same "natural, not corpus-style or
academic-register" rule `prompts.SENSES_INSTRUCTIONS` gives the original senses stage,
made explicit here because CORE-DIARY's own raw-quality notes flagged stilted
"Researchers must…" examples as a defect worth not repeating. Each returned sentence
becomes a canonical `(neutral, plain)` example; its span is located with the same
`spans.find_span` call the `spans` pass itself uses, and a sentence the finder cannot
place is kept anyway with `span=None` — the `spans` pass's own model fallback gets
another try at it on a later sweep, so nothing here duplicates that pass's job. Step (b)'s
call is stamped `stage=StageName.HYGIENE`, reusing that stage's model policy (nano, low
effort, per `config.py`) rather than adding a `StageName` member and a `config.py` policy
entry for one call site; because that makes its provenance record's `stage` field
identical to the hygiene pass's own step-(c) record, idempotence for step (b) cannot be
`_has_run`-style stage matching — a private sentinel on the record's `note` field
(`_REPAIR_EXAMPLES_NOTE`), checked by a bespoke `_has_repaired`, does the job instead. As
with `hygiene`'s step (c), the pass's one small output contract and its instructions
(`_DraftRepairExamples`, `REPAIR_EXAMPLES_INSTRUCTIONS`) are module-private in
`retrofit.py` rather than added to `contracts.py`/`prompts.py`: a single self-contained
call site with no other dependents has no reason to grow either module. `PassResult.metrics`
gains `senses_retired`, `examples_added`, and `entries_needing_examples` (the last counted
whether or not a call was actually made, so a re-run's audit value stays honest even when
the marker suppresses the bill).

**Consequence.** A store swept with `repair` after `spans` has no two non-retired senses
per entry sharing a gloss and no non-retired sense with zero canonical examples that the
model could answer for; a second sweep costs nothing on both counts. `resolve_store` and
every rendition sweep now see a cleaner sense inventory without themselves changing.
Nothing here touches `data/core-store` — the pass is new code, exercised only by
`tests/test_retrofit.py`'s offline `scripted_model` fixture against `tmp_path` stores.

## D-38 (2026-09-02) — `resolve` cost fix: instructions past the cache floor, reasoning off, no free-text field

**Context.** A live `resolve_store` run measured 7,474 calls at `cache_hit_rate` 0.0015,
≈2,515 input tok/call, ≈655 output tok/call, and $0.00066/call — 2.5× the cost model's
per-call estimate (`docs/COST-MODEL.md`, resolve row). Two causes, both structural, not
provider variance: `RESOLVE_INSTRUCTIONS` was 1,117 characters (≈279 tokens), well under
OpenAI's 1,024-token prompt-cache-prefix floor, so it never cached regardless of call
volume or a stable `openai_prompt_cache_key`; and `reasoning_effort="low"` on a
three-field structured-output contract (`target_ref`, `sense_choice`, `confidence`) was
paying for hidden reasoning tokens — billed as output, invisible in the answer — on every
call, which is why output ran to ~655 tokens/call against a contract that needs at most a
few dozen.

**Decision.** Three changes, `prompts.py` (`PROMPT_VERSION` "5" → "6"):

1. `RESOLVE_INSTRUCTIONS` rewritten as a static, byte-stable block of 7,341 characters
   (≈1,624 tokens by `tiktoken`) — comfortably over the cache floor. The added length is
   substance, not padding: decision rules for reading the source gloss against each
   candidate's own gloss (never the target's bare surface form), a part-of-speech
   preference (with `derivation` exempted, since it is expected to cross parts of
   speech), a three-band confidence rubric anchored to concrete criteria (0.85-1.0
   unambiguous, 0.5-0.84 a best guess among plausible competitors, below 0.5 a genuine
   guess), and a worked example over three targets — a clear synonym match, an
   apparently-common but actually-unambiguous hypernym, and a decline where the only
   candidate is an unrelated homograph. The final section states explicitly that the
   answer carries only the choice and the confidence, and that the worked example's
   "Reasoning" lines are pedagogy, not the answer format.
2. `contracts.py`'s `DraftTargetResolution` gets no new field and drops none — it never
   carried a `reason`/`explanation` field to begin with, so there is nothing here for a
   nano model to be tempted into filling with prose. Its docstring now says so
   explicitly, and `sense_choice`/`confidence` each get a one-line `description` (the
   existing pattern elsewhere in `contracts.py`) reinforcing the instructions' contract
   at the schema level.
3. `config.py`'s `RESOLVE` policy moves `reasoning_effort` from `"low"` to `"none"` —
   confirmed accepted by `OpenAIResponsesModelSettings.openai_reasoning_effort`
   (`Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None`) and by
   `gpt-5.4-nano`'s model profile (`openai_supports_reasoning_effort_none: True`), which
   disables reasoning outright rather than merely reducing it. The same change applies to
   `CLASSIFY_KIND`, `TAG_DOMAIN`, `SPANS`, and `FRONTIER` — every other nano stage whose
   output is an enum, an integer, or a pair of offsets, not prose — for the same reason.
   `HYGIENE` is left at `"low"`: it edits and rewrites gloss text, closer in kind to the
   prose stages than to this group. `max_tokens=2048` is unchanged on every policy in
   this group.

**Consequence.** Expected effect, worked from the measured 2,515 tok/call input split
(`docs/COST-MODEL.md`, resolve row, "Measured live" subsection): cached input at 1,624
tok/call and 1/10 price, fresh volatile input unchanged at ~2,236 tok/call, output capped
by the tightened contract and disabled reasoning at ≤150 tok/call — an expected total of
≤$0.00034/call against the measured $0.00066/call, roughly half, with almost the entire
saving coming from the output-token line (output alone was $0.0004094/call measured,
more than the whole expected total after the fix). No change to `resolve.py`'s behaviour:
the contract's shape and field names are unchanged, so `resolve_entry`/`resolve_store`
need no update beyond the instructions and policy swap; a scripted test model's payload
(`tests/conftest.py::_resolution_payload`) already matches `DraftTargetResolution`
exactly, since it never populated a field this decision removed. `workflows/retrofit.py`,
`schema.py`, and `data/` are untouched — this is a prompt- and policy-only fix, verified
against a live store with a scratch copy so `data/core-store` was never written.

## D-39 (2026-09-02) — Headword-initial renditions: one shared detector, one shared retry, one retrofit pass

**Context.** `docs/CORE-DIARY.md` iteration 4's quality snapshot measured the defect on
400 swept core entries, proper nouns excluded: canonical glosses open by naming their own
headword 2.7% of the time — the `hygiene` pass having already done its work — but their
*renditions* do so 10.2% (grade_1), 15.4% (grade_5), 15.3% (grade_10), 13.9% (college) and
~10.5% at every register. `RENDITIONS_INSTRUCTIONS` forbids it in as many words ("Never
begin a definition rendition with the headword, with 'the word X', or with 'X is'"), and
the instruction is not enough: a ten-word sentence budget at grade_1 and a "make it
punchy" register at neutral both pull the model straight to "A ban is an order to stop."
The rewrite that produced the source text is clean; the rewrite *of* it is not, which is
why the canonical rate and the rendition rate differ by a factor of five.

Three code paths already needed the same predicate and only one of them had it: the
`hygiene` pass's step (c) carried a private `_gloss_offends`, `audit.py` compared first
words, and the renditions workflow had nothing at all.

**Decision.** Four changes, no prompt-version bump — `RENDITIONS_INSTRUCTIONS` is
unchanged, so the cached prefix every rendition call shares is unchanged with it.

1. **One detector, in one place.** `hygiene.py` (new module) exports
   `is_headword_initial(text, headword)`. It is `_gloss_offends` moved out, plus two cases
   the measurement showed matter: a plural `-s` on the headword, and a leading article
   ("A ban is …", "The people are …") — the single most common shape in the swept sample,
   and one the old bare-headword pattern missed entirely. `workflows/retrofit.py`,
   `workflows/enrich.py` and `audit.py` import it; nobody re-implements it. Proper nouns
   stay exempt (D-30) and the exemption stays the *caller's*: the function is given a text
   and a headword and has no entry to ask about `kind`.
2. **Generation time (`workflows/enrich.py`).** A gloss rendition of a non-proper-noun
   entry that opens with its headword is a miss, treated exactly as a readability miss is:
   the failing targets — and only those — are re-requested once, the better candidate is
   kept, both calls are priced, and a `rendition_headword_initial` event is logged with
   `fixed` true or false. What still opens badly after the retry carries
   `QAFlag.OG_HEADWORD_INITIAL`, the same contract `OG_READABILITY_MISS` has. The two
   checks deliberately share the *one* retry: a target failing both is re-requested once
   with both feedback sections, never twice, which is what keeps the worst case at two
   calls per `(owner, field)` rather than three. Where the two disagree about which
   candidate to keep, not opening with the headword outranks reading easier — a grade is
   continuous and an opening is right or wrong, so they are ordered rather than blended.
   `config.readability.headword_initial_retry` (default `True`) switches it off,
   independently of `enabled`, which governs the band check.
3. **What is already on disk (`workflows/retrofit.py`).** A sixth pass,
   `rendition_hygiene`, appended to `RetrofitPass.ALL` after `repair`: for each
   non-proper-noun entry, one nano call listing every offending stored gloss rendition
   with the reading level and register it must stay at, `{rewrites: [{rendition_ref,
   text}]}` back, markdown stripped, Flesch-Kincaid re-measured into the rendition's own
   `Assessment`, and `OG_HEADWORD_INITIAL` set or cleared to match what the new text
   actually is. The superseded text is kept in a zero-cost `Provenance.note`, exactly as
   `hygiene` keeps a superseded gloss. Idempotence is by marker, and because this pass's
   call is stamped `stage=StageName.HYGIENE` — it reuses that policy rather than adding a
   stage for one call site, as `hygiene` step (c) and `repair` step (b) do — the marker is
   a private note sentinel, `_RENDITION_HYGIENE_NOTE` (amended by D-47: the pass runs last
   of all, and the sentinel carries the offending set's digest rather than a bare
   "rewritten"). It is also the one pass that writes
   its entry when its call changed nothing: the marker is the only thing that call bought,
   and dropping it would re-bill the same answer on the next sweep.
4. **Measurement (`audit.py`).** A read-only `gloss_renditions_headword_initial` count and
   percentage over non-canonical gloss renditions, proper nouns excluded, so the diary can
   state a before and an after rather than a hope. It sits beside
   `gloss_starts_with_headword`, which stays exactly what it was — the canonical-gloss
   number — so the two rates the finding is about remain separable.

**Consequence.** Expected cost of the retrofit pass over the core: ~55K offending
renditions across ~10K entries, one call per entry at ~$0.0002 (nano, `low` effort, an
instruction block that caches) ≈ **$2**, against the ~$5.12 sweep 2 alone cost to write the
renditions in the first place. Generation-time cost is bounded by the retry that already
existed: a target that would have been retried for readability now sometimes carries a
second note in the same call, and a target that fails only the new check adds one retry to
a `(owner, field)` that previously made none — on the measured ~13% offender rate, well
under one extra call per entry. Nothing here touches `data/core-store`: the pass is new
code, exercised only by `tests/test_retrofit.py`'s offline `scripted_model` against
`tmp_path` stores, and `enrich.py`'s check is exercised by two new marker headwords in
`tests/conftest.py`. Tests: 449 → 486.

## D-40 (2026-09-02) — Two free graph checks in `audit.py`: hypernym acyclicity and symmetric-relation reciprocity

**Context.** Pristine item 9 (`docs/CORE-DIARY.md`) has always named "hypernym graph
acyclic within the core" as one of its free consistency checks, but nothing measured it:
`audit_store` counted relation resolution and artifact targets, never walked the
resolved-relation graph itself. A `synonym`/`antonym`/`confusable_with` relation is
supposed to hold in both directions by construction — if `vow` calls `promise` a synonym,
`promise` should call `vow` one back — and that too had no measurement, only the hope that
`resolve` and hand-written relations got it right. Both are read-only questions over data
already in the store: no model call, no schema change, no new pass to write anything.

**Decision.**

1. **Hypernym acyclicity.** `_build_hypernym_graph` projects every resolved `hypernym`
   relation as a sense→sense edge in the direction it already points, and every resolved
   `hyponym` relation reversed (`A`'s hyponym relation to `B` means `B` is more general,
   so it contributes the edge `B→A`) — one graph, regardless of which of the two relation
   types asserted a given edge. `_find_hypernym_cycles` walks it with an iterative DFS
   (explicit `(node, neighbor-iterator)` stack, three-color marking) rather than a
   recursive one: the graph can hold on the order of 40K sense nodes at core-list scale,
   well past a safe Python recursion depth. Each "gray" neighbor found while it is still
   on the stack closes exactly one cycle, and up to 5 of those are kept verbatim as
   sense-id lists for `hypernym_cycles.examples`; the total count is every back edge
   found, not deduplicated by shared membership, since two overlapping cycles are two
   separate defects to fix. Unresolved targets contribute no edge — an unresolved relation
   cannot close a cycle it is not part of. `hypernym_self_loops` is the same graph's edges
   whose two ends share a lexeme (via `sense_id`'s `lexeme:pos:index` prefix): nothing is
   its own hypernym, whichever of its senses does the pointing or whichever of the two
   relation types said so.
2. **Reciprocity.** For every resolved relation of a symmetric type
   (`RelationType.SYNONYM`, `ANTONYM`, `CONFUSABLE_WITH`) whose target lexeme is also among
   the audited entries, `_audit_reciprocity` checks whether that target entry asserts the
   same type back toward the source lexeme in any of its own senses — resolved or not: the
   question is only whether the claim exists on the other side, not whether `resolve` has
   found it yet. Both directions of an already-mutual pair are counted as two separate
   assertions, each satisfied by the other; a one-sided claim is asserted with zero
   reciprocation. This is a measurement only — `audit_store` still never writes a relation,
   here or anywhere else.
3. Both land under `as_dict()["graph"]`: `hypernym_cycles` (`count` and `examples`),
   `hypernym_self_loops`, and `reciprocity` (per type, `asserted`/`reciprocated`/`pct`).
   `top_gaps` treats a cycle as what it is — a defect, not a shortfall with a percentage —
   so it is never folded into the shortfall sort; when `hypernym_cycle_count > 0` its line
   is simply prepended, ahead of whatever the sort ranks first.

**Consequence.** Zero cost: both checks run over data already in memory during the
existing audit pass, one extra graph build and one extra scan per `audit_store` call.
Nothing here touches `data/core-store` — `audit.py` remains read-only, exercised entirely
by hand-built `tmp_path` stores in `tests/test_audit.py`. Tests: 486 → 494.

## D-41 (2026-09-02) — Budget reservations priced at `max_tokens` starved dispatch far below the ceiling

**Context.** A live `enrich` sweep at `--budget 8 --concurrency 128` stopped with
`stop_reason=budget` at $4.96 actually spent — 62% of the ceiling, unspent budget left
on the table for no reason a user could see in the summary. `StageRunner._attempt`
(`stages.py`) reserves each in-flight call's cost with
`estimate_cost(..., output_tokens=policy.max_tokens)` before dispatch, so that
`BudgetGuard.reserve` (`budget.py`) can refuse admission before the ceiling is actually
breached (§ DESIGN.md 4.2). The RENDITIONS policy sets `max_tokens=8192` because a
four-target rewrite can genuinely need that much room (`config.py`'s own note on why
prose stages get 8192, not 4096); measured output is ~250 tokens/call
(docs/COST-MODEL.md's renditions arithmetic). With `--concurrency 128` and
`enrich`'s `_add_renditions` fanning every missing `(owner, field)` out concurrently
per entry rather than one at a time, far more than 128 RENDITIONS calls can be
in flight together, each holding a reservation ~30x its true cost. The guard was doing
exactly what it was built to do — refuse when `committed + reserved + estimate >
budget` — the reservation itself was the wrong number.

**Decision.** Add `ModelPolicy.expected_output_tokens: int` (default 512, validated
`<= max_tokens`), set per stage from the measured figures in docs/COST-MODEL.md and
docs/CORE-DIARY.md Iteration 4 (renditions 400, senses 600, encyclopedia 1600,
etymology 400, overview 200, lexical_explanation 150, the nano structural stages
100-200, hygiene 300, QA 800 — each rounded up from measurement with a safety margin,
still far under its stage's `max_tokens`). `_attempt` now reserves the budget guard at
`expected_output_tokens` instead of `max_tokens`; the rate limiter's own token
reservation (protecting TPM, which the provider enforces regardless of what a call
actually uses) is untouched and still reserves at `max_tokens`, because over-reserving
there only costs throughput, not correctness — the asymmetry is documented at the call
site in `stages.py`. `BudgetGuard.reserve` also now logs `budget_reservation_refused`
at info level with `committed_usd`, `reserved_usd`, `estimate_usd`, and `budget_usd`
whenever it refuses, so a future early stop is diagnosable from the run log instead of
inferred from a summary number, the way this one was.

**Consequence.** A scenario test (`tests/test_budget.py`) reproduces the reported
shape: 128 concurrent reservations of a RENDITIONS-shaped call (`max_tokens=8192`,
`expected_output_tokens=400`) against a run that has already committed $7.50 of an $8
ceiling. Priced at `expected_output_tokens`, all 128 are admitted; priced at
`max_tokens` — the pre-fix formula — some are refused, confirming the mechanism. No
change to when a run's *actual* spend stops it: `expected_output_tokens` only sizes the
provisional hold, and `BudgetGuard`'s own tests (concurrent overshoot, release,
idempotent release, unlimited budget) are unchanged. Separately, `tests/test_cli.py`'s
`_offline` fixture now points `OPENGLOSS_LOG_DIR` at `tmp_path` and asserts no file
appears under the repo's `runs/` directory during a test, cleaning up a pre-existing
leak (`AppConfig.log_dir` defaults to `runs`, relative to cwd) that had left thousands
of ledger/log files in the repository; none of them were touched. Nothing here touches
`data/core-store`. Tests: 494 → 496.

## D-42 (2026-09-02) — `readability_hygiene` retrofit pass: a targeted second rewrite for renditions still out of band

*Amended by D-47: this pass now runs **before** `rendition_hygiene`, not after it, its
instructions carry the headword-initial rule, and its marker is keyed on the flagged set.*

**Context.** Generation time already measures every rendition's Flesch-Kincaid grade and
retries once, for `grade_1` and `grade_5`, when it misses its band by more than
`config.readability.tolerance` (`workflows/enrich.py`, docs/CORE-DIARY.md's readability
finding). The retry is not free and is deliberately not a loop — "nearly all of the
improvement is" in the first one — so pristine check 9 ("readability of each rendition
within its band") is left with a residue: the ~4-8% of renditions that still miss after
that one retry, each carrying `QAFlag.OG_READABILITY_MISS` and sitting on disk exactly as
generated. Nothing revisited them; `rendition_hygiene` (D-39) reads the same renditions
but only for the unrelated headword-initial defect. This is the same shape D-39 itself
answered for that other defect: a generation-time check with a bounded retry, and a
retrofit pass for what the retry did not fix.

**Decision.** A seventh pass, `readability_hygiene`, appended to `RetrofitPass.ALL` after
`rendition_hygiene` — the more expensive of the two rendition-reading passes runs last, so
it never spends fixing text `rendition_hygiene` is about to rewrite for its own defect.
For each entry, every rendition of every text-bearing field (sense gloss, sense example,
the entry's encyclopedia section, its lexical explanation) whose assessment still carries
`OG_READABILITY_MISS` is collected and rewritten in one call — on the `RENDITIONS` policy
(luna), not `hygiene`'s nano, since this is prose for an audience rather than a structural
verdict — split into two (or more) calls only when the flagged set's source text passes
roughly 3,000 words, so an encyclopedia-heavy entry's call is never truncated. The prompt
lists each offender as its field, its reading level and register, and the grade it
measured against its band's upper bound, then reuses — never restates — two existing
texts verbatim: the "READING LEVELS." and "WHAT THE FIELD MEANS FOR YOUR OUTPUT." sections
of `RENDITIONS_INSTRUCTIONS` (sliced out at import time, so the two can never drift apart),
and `build_readability_feedback`'s own wording for the per-level feedback line. Every
returned rewrite is markdown-stripped, re-measured with the headword scored as one
syllable, and kept only if its grade is lower than what is already stored — the better of
old and new, mirroring the generation-time retry's own rule, never a blind overwrite. An
example's rewrite carries one more condition: `spans.find_span` is re-run over it, and a
rewrite the finder cannot place — the rewrite lost the headword — is discarded outright,
the old example kept untouched whatever its grade. Whatever text ends up stored is
re-scored and `OG_READABILITY_MISS` set or cleared against the same `tolerance`, so the
flag never lags the text it describes. The superseded text is kept in a zero-cost
`Provenance.note`, exactly as `rendition_hygiene` keeps one; idempotence is by a private
note sentinel (`_READABILITY_HYGIENE_NOTE`) rather than the stage alone, since the call is
stamped `stage=StageName.RENDITIONS` — the same stage `enrich.py`'s own rendition
generation uses, whose ordinary records carry no note at all, so the two cannot collide.
An entry with no flagged renditions is skipped at $0, as is a second sweep over one this
pass already tried.

**Consequence.** Expected cost over the core at the assumed miss rate: ~25K flagged
renditions across ~8K entries, one call per entry (a handful split into two for
encyclopedia-heavy entries) at ~$0.0004 (luna, `low` effort, an instruction block that
caches) ≈ **$3.20** — cheap relative to the ~$0.002/word generation cost this residue is
riding on top of, because it only ever touches the renditions that already needed a second
look. Nothing here touches `data/core-store`: the pass is new code, exercised entirely by
`tests/test_retrofit.py`'s offline `scripted_model` against `tmp_path` stores, with one new
`_payload_for` branch in `tests/conftest.py` and two marker headwords (one whose scripted
rewrite is simple enough to clear the flag, one whose rewrite never mentions the headword
so an example rewrite is discarded). Two pre-existing ordering tests were updated for the
new seventh pass name; nothing else changed shape. Tests: 496 → 503.

## D-43 (2026-09-02) — `graph_hygiene`: the hypernym graph is repaired deterministically, by demotion, for $0

**Context.** D-40 added two free graph checks to `audit.py` and deliberately stopped
there: it measures, it never writes. Iteration 6 then measured them on the 10K core
store, over resolved relations only, and the hypernym graph turned out not to be a
hierarchy at all. 58,292 hypernym edges (a `hypernym` relation in the direction it
points, plus a `hyponym` relation reversed) carry 40 same-lexeme self-loops; 458 mutual
pairs where `A` calls `B` its hypernym and `B` calls `A` its hypernym back, at mean
confidence 0.87 in *both* directions (`resource ↔ supply`, `explanation ↔ reasoning` —
sibling terms the model could not order, not a hierarchy it got backwards); 134 cyclic
components of size 2, 41 of size 3, about 25 of sizes 4–11, and one tangled component of
2,840 senses, dominated by verbs (`settle`, `compromise`, `reached`). Separately,
`synonym` is reciprocated 24% of the time and `antonym` 13%, although the reverse of a
symmetric relation is implied by its own definition. None of this needs a model to fix:
the defect is visible in the relation types, the resolved sense ids, and the confidences
already on disk.

**Decision.** A sixth workflow, `workflows/graph_hygiene.py`, exposing
`run_graph_hygiene(store, runner, *, workers, stop_event=None, dry_run=False)`. The
signature is `run_retrofit`'s, but `runner` is accepted only for parity and is never
used: the pass makes no model calls at all, and `None` is the expected value. The whole
store is read once into a relation-only projection (the parsed `Lexeme` objects are
dropped as they are read, so the working set is the graph, not the renditions), the edit
plan is computed in memory, and only then is anything written — one entry per work item
through `run_pool`, re-read and written inside one hold of its own lock (D-31). Because
the plan came from a read taken outside that lock, every edit names its relation by
position *and* by content, and one that has moved is re-located, one that is gone is
skipped.

1. **Nothing is deleted; defective edges are demoted** (D-1's spirit — information is
   tombstoned, not removed). A self-loop and a cycle-breaking back edge become
   `see_also`; a mutual pair becomes `synonym`, which is what the two claims together
   actually say. The reason goes in `Relation.note` (`"demoted: self-loop"`,
   `"demoted: mutual hypernym"`, `"demoted: cycle break (conf=…)"`), and every demoted
   relation on an entry points at one shared zero-cost `Provenance` record
   (`stage=hygiene`, `model="rule:graph_hygiene"`). The demoted types are outside
   `audit._build_hypernym_graph`'s projection, which is exactly what makes the pass
   idempotent: a second sweep rebuilds the graph, does not find those edges in it, and
   plans nothing.
2. **The one case where a demotion cannot be a promotion in disguise.** A mutual
   assertion whose sense *already* asserts `synonym` toward the same lexeme cannot
   become a second one — same `edge_id`, two edges. It is demoted to `see_also` instead
   of being dropped outright, with `"demoted: mutual hypernym (synonym already
   present)"`, so the "nothing is lost" rule holds for it too.
3. **Cycle breaking is greedy min-FAS, computed by incremental topological order.** Only
   an edge internal to a non-trivial strongly connected component can lie on a cycle, so
   an iterative Tarjan (explicit stack, for the 40K-node reason `audit.py` already gives)
   finds the components, and each component's internal edges are offered *best-first* to
   a topological order maintained by Pearce and Kelly's dynamic-topological-sort
   algorithm (ACM JEA 11, 2006). An edge that fits is kept; one that would close a cycle
   against the edges already kept is refused, and the refusal *is* the removal — which is
   the greedy minimum feedback arc set stated the other way round, since a refused edge
   is always the last, hence worst, edge on the cycle it closes. "Best" is a total order,
   so no outcome depends on dictionary or set iteration order:
   `(confidence, -out_degree(source), source_sense_id, target_sense_id)`, removed
   lowest-first, with an unscored edge treated as confidence 1.0 so a hand-written
   relation is the last thing taken. The direct phrasing of the same greedy (find a
   cycle, remove its worst edge, repeat) is `O(k · (V + E))` for `k` removals and is not
   affordable: on a synthetic 2,840-node component with 10,506 internal edges it took
   32 s and removed 7,084 edges, against **0.09 s and 1,708 edges** for the incremental
   order — faster *and* a smaller arc set.
4. **Reciprocity is completed, not just measured.** For a resolved `synonym`, `antonym`
   or `confusable_with` relation `A → B` whose target entry asserts nothing of that type
   back toward `A`'s lexeme — the same entry-level question `audit._audit_reciprocity`
   asks, and resolution is not required on the far side — the reverse is written on
   `B`'s own sense: `A`'s headword as the term, `A`'s sense id already filled in (the
   reverse of a resolved relation needs no resolution of its own), the same confidence,
   `note="reciprocal of <A sense id>"`, and a zero-cost `Provenance` with
   `stage=hygiene`, `model="rule:reciprocity"`. A `confusable_with` reverse carries the
   original note as well as its own, since that type's note is its content and the schema
   requires one. Step 4 runs over the projection *after* steps 1–3 have retyped it, so a
   mutual pair demoted to `synonym` is seen as the reciprocal pair it has become and
   gains nothing further.
5. `GraphHygieneOutcome` reports `entries_scanned`, `entries_changed`, `hypernym_edges`,
   `self_loops_demoted`, `mutual_demoted`, `cycle_edges_demoted`, `sccs_broken` and
   `cycle_edges_by_scc_size` (both per size bucket: `2`, `3`, `4-11`, `12-99`, `100+` —
   the core store's own distribution is why the small sizes get their own), and
   `reciprocal_added` per relation type. `dry_run=True` computes the entire plan and
   writes nothing.

**Consequence.** Zero cost and no new stage: the two provenance markers reuse
`StageName.HYGIENE` with a rule name in `model`, the way `retrofit.DETERMINISTIC_MODEL`
does, so nothing about model routing or the cost table changes. The pass is verifiably
reproducible — the same store repaired under three different `PYTHONHASHSEED` values
produces byte-identical relations — which matters because the graph is built from `dict`
and `set` structures keyed by strings, whose iteration order is randomised per process;
every adjacency list is sorted and every choice is a total order for that reason.
Nothing here touches `data/core-store`: the whole suite runs on hand-built `tmp_path`
stores, including a 200-node synthetic component asserted to break in under five seconds
and to leave `audit._find_hypernym_cycles` reporting zero. The CLI wiring and a
`RetrofitPass` entry are deliberately not part of this change. Tests: 505 → 527 (22 new
in `tests/test_graph_hygiene.py`; the baseline moved under concurrent work on the other
passes).

## D-44 (2026-09-02) — `taxonomy.py`: 10 new leaves for the four largest `.general` buckets; `TAXONOMY_VERSION` added

**Context.** On the 10K core, 21.0% of 38,955 non-retired senses (8,164) carry a
`.general` leaf. Four roots account for 68% of that residue:
`everyday_life.general` (3,372), `people_society.general` (1,298),
`business.general` (618), `mathematics.general` (546, not touched here — see below),
`law_government.general` (452), `language.general` (352), `technology.general`
(144). A read-only sample of 60 senses each from `everyday_life`, `people_society`,
`business`, `technology` (20 each from every other root) via
`LexemeStore(StoreConfig(root=Path("data/core-store"))).iter_entries()`, filtered on
`taxonomy.is_general`, showed the residue is not noise — it clusters tightly into
concepts the existing 8-12 leaves per root have no slot for:

- `everyday_life.general`: generic action/state verbs with no domestic-topic content
  (*keep, adapt, transform, stay, serve, continuing, arrived, seized*) and
  quantity/size/date/duration words (*ton, couple, portion, diff, twenty, several,
  identical, minutes, soon, ago, monday, october, quarterly, last, preliminary*).
- `people_society.general`: personal given names and surnames (*adam, francis, neil,
  roland, teddy, henry, julie, jennifer, sandra, hugh, joshua* — ~18% of the sample),
  personality/character adjectives (*rigid, rude, ugly, cruel, noble, successful,
  distinguished, unaware, fortunate*), social titles/positions (*outsider, dame,
  ranger, admiral, bishop, agent*), and attitude/emotion words (*wonder, dare,
  insist*).
- `business.general`: value/quality/pricing vocabulary with no dedicated leaf
  (*rates, rankings, grades, quality, price, cheap, luxury, expertise, capable,
  stable, surplus, costs*) and the bare concept of a job/career, distinct from HR
  process (*job, worker, careers, employ, specialists, consulting, publishing,
  nonprofit*).
- `technology.general`: generic device/system operational states with no home under
  the physical-hardware-nouns leaf (*automated, activated, deployed, mobile,
  handheld, mute, external, independent, mode, batch, upgrade, launch, setup,
  obsolete, duplicate, compound, layer, substitute, tracker, identifier*).
- `language.general` (20-sample, not one of the four biggest, but an unambiguous
  cluster and within the existing 8-12 cap to fix): bare communication acts
  (*conversation, discuss, asks, questions, talking, telling, spoke, reported*) with
  no leaf between `vocabulary` (word meanings) and `rhetoric` (persuasion).

`mathematics.general` (546) and `law_government.general` (452) were sampled but not
given new leaves: their residue is dominated by the same cross-cutting generic-verb/
quantity vocabulary already addressed by `everyday_life`'s two new leaves (*express,
identical, single, several, twenty, year, last*) or by senses that already fit an
existing leaf and are a `tag_domain` prompt-quality problem, not a taxonomy gap
(*duties* -> `civics`, *dismiss* -> `courts_justice`) — adding roots' worth of new
leaves for those would duplicate `everyday_life`'s new leaves under a different root
for no distinguishing content.

**Decision.** Add 10 leaves, five-word `GLOSSES` entries included so
`TAXONOMY_PROMPT_BLOCK` picks them up automatically:

| Root | New leaf | Gloss |
|---|---|---|
| `business` | `business.value_quality` | pricing value quality and worth |
| `business` | `business.occupations_careers` | jobs careers professions and occupations |
| `everyday_life` | `everyday_life.actions_routines` | everyday actions habits and routines |
| `everyday_life` | `everyday_life.quantity_time` | amounts sizes dates and duration |
| `language` | `language.communication` | conversation discussion and spoken interaction |
| `people_society` | `people_society.personal_names` | given names surnames and nicknames |
| `people_society` | `people_society.character_traits` | personality traits character and temperament |
| `people_society` | `people_society.social_roles` | titles ranks and social positions |
| `people_society` | `people_society.emotion_attitude` | emotions attitudes and personal feelings |
| `technology` | `technology.device_operation` | device settings modes and operation |

`everyday_life` (12 -> 14) and `people_society` (10 -> 14) exceed the previous 8-12
leaves-per-root band; `business` (10 -> 12), `language` (10 -> 11) and `technology`
(10 -> 11) stay within it. `tests/test_taxonomy.py`'s per-root leaf-count assertion is
raised to an 8-14 band, gated to only `everyday_life`/`people_society` by a new
`test_only_the_documented_roots_use_the_raised_leaf_cap` test, so a future root
drifting past 12 without justification still fails loudly. Total `LEAF_COUNT`: 150 ->
160, still inside the existing 130-180 test band. No existing leaf is renamed or
removed — stored `Sense.domain` values reference them by string and a rename would be
a silent-corruption migration, not a taxonomy edit.

Also added `taxonomy.TAXONOMY_VERSION: str = "2"`, bumped whenever a leaf is added.
This is the fix for a live cost bug in `workflows/retrofit.py`'s `hygiene` pass: step
(d) (`_clear_weak_domains`) clears every sense whose `domain` is its root's `.general`
catch-all unconditionally, on every sweep, so a sense correctly tagged `.general`
under the *current* (post-D-44) taxonomy gets cleared and re-tagged by `tag_domain`
again next run for no reason — a real, recurring cost (~$0.6/loop per the task brief)
with no convergence. The fix (described here, not implemented — another agent is
editing `retrofit.py` concurrently): record `taxonomy_version=<TAXONOMY_VERSION>` in
the `tag_domain` stage's `Provenance.note` when `_tag_entry` calls
`entry.add_provenance(stage_result.provenance)` (retrofit.py:1221), the same
`model_copy(update={"note": ...})` pattern `_REPAIR_EXAMPLES_NOTE` already uses at
line 1557; then change `_clear_weak_domains`'s condition from `is_general(sense.domain)
or legacy_mapped` to `is_general(sense.domain) and (not _has_run(entry,
StageName.TAG_DOMAIN) or _tag_domain_note_version(entry) != TAXONOMY_VERSION) or
legacy_mapped` — i.e. clear a `.general` tag only when there is no `tag_domain`
verdict at all, or the most recent one predates the current taxonomy version; a
`.general` verdict stamped with today's `TAXONOMY_VERSION` is left alone because the
tagger already had every current leaf (these 10 included) to choose from and chose
`.general` on purpose.

**Consequence.** No stored data touched — this is a taxonomy-definition-only change;
existing `Sense.domain` values keep meaning exactly what they meant before. The next
`retrofit tag_domain` sweep (once the step-(d) fix above lands) will have real leaves
to place the sampled clusters in, expected to cut the `.general` share materially on
`everyday_life` and `people_society` in particular. Tests: 496 -> 498
(`test_only_the_documented_roots_use_the_raised_leaf_cap`,
`test_taxonomy_version_is_a_nonempty_string`); `test_every_root_has_a_general_leaf_and_8_to_12_leaves`
renamed to `..._8_to_14_leaves` and its bound parameterized per root.

## D-45 (2026-09-02) — An example that never uses its own headword is a miss too, generation-time and on disk

**Context.** A live measurement on the 10K core (`docs/CORE-DIARY.md` Iteration 6's
correction to finding #4) found 2,921 example renditions with no found span, across
every field target, canonical included; 2,575 of those contain no form of the headword
at all — the model wrote around the word entirely ("custody" -> "The judge let both
parents care for their child."; "properties" -> "Dad owns two houses near our school."),
worst at grade_1 (820) and in the v1.3 canonicals (991). `RENDITIONS_INSTRUCTIONS`
already says an example "must contain the headword or an inflected form of it," and
`SENSES_INSTRUCTIONS` says the same for the original canonical examples; as with D-39's
headword-initial defect, stating the rule is not what enforces it — nothing ever checked
whether a returned example actually obeyed it. `spans.find_span`'s job is placing a
*character span* for a headword that is already there; it was never asked whether one is
there at all, and neither generation-time path (`enrich.py`'s rendition rewrites, or the
original `senses`/`repair` example text) rejected a sentence that used none of the
sense's own forms.

**Decision.** Two changes, exactly D-39's shape applied to a third check rather than a
second, and no `RENDITIONS_INSTRUCTIONS`/`PROMPT_VERSION` change — the instruction text
already states the rule; only enforcement is new.

1. **Generation time (`workflows/enrich.py`).** An `examples`-field rendition whose text
   `spans.find_span` (given the sense's `Morphology.inflected_forms()` plus
   `spans.generate_forms(headword)` as candidates) cannot place at all is a miss, sharing
   the exact retry mechanism D-39 built for headword-initial glosses: `_Measured` gets a
   third verdict field, `headword_absent`, measured once per candidate exactly as
   `headword_initial` is; a target failing any combination of the three checks (band,
   initial, absent) is re-requested once, `_build_feedback` growing a third optional
   section from the new `prompts.build_headword_absent_feedback(headword)`; and
   `_is_better`'s ordering gains a third tier — not opening with the headword and not
   using it at all both outrank reading easier, with the two hard-defect checks disjoint
   by construction (`headword_initial` only ever applies to `gloss`, `headword_absent`
   only ever to `examples`, so a target never carries both verdicts at once). What is
   still absent after the retry carries the new
   `QAFlag.OG_HEADWORD_ABSENT = "og.headword_absent"`, the fifth `og.`-prefixed member of
   the closed enum. Unlike headword-initial, there is **no proper-noun exemption**: an
   example sentence has to use its headword whatever kind of entry it illustrates, so
   `_checks_headword_absent` looks only at `policy.headword_absent_retry` (new,
   `ReadabilityConfig.headword_absent_retry: bool = True`, independent of `enabled` like
   its D-39 sibling) and the field, never at `entry.kind`.
2. **What is already on disk (`workflows/example_hygiene.py`, new module).**
   `run_example_hygiene(store, runner, *, workers, stop_event=None)` visits every entry
   once and, for each, lists every example rendition — any reading level or register,
   canonical included — whose span is `None` *and* whose text `find_span` cannot place
   either, with the owning sense's canonical gloss for context, in one call on the
   `RENDITIONS` policy (luna, not `hygiene`'s nano — this is audience-held prose, not a
   structural verdict). Each returned sentence is markdown-stripped, re-checked with
   `find_span`, and adopted only if a span is actually found — the same "the fix must be
   verified, not trusted" discipline `retrofit.py`'s `readability_hygiene` pass applies
   to its own example rewrites — with the grade re-measured into the rendition's own
   `Assessment` and `OG_HEADWORD_ABSENT` set or cleared to match. Superseded text is kept
   in a zero-cost `Provenance.note`, idempotence is a private note sentinel
   (`_EXAMPLE_HYGIENE_NOTE`), and an entry with nothing to fix costs $0 — all three
   exactly `rendition_hygiene`'s and `readability_hygiene`'s own conventions. The module
   is new and self-contained (imports nothing from, and is imported by nothing in,
   `workflows/retrofit.py`) because two other passes of work are landing in that module
   concurrently on this branch; wiring `retrofit --only example_hygiene` into a
   `RetrofitPass` member is left for whoever next touches `retrofit.py`/`cli.py`, and
   `run_example_hygiene`'s signature (store, runner, worker count, optional shared stop
   event, optional explicit id list) is written to be callable exactly the way that
   wiring will need it.

**Consequence.** Expected cost of the retrofit pass over the core: ~1,500 affected
entries, one luna call each at the renditions-stage rate ≈ **$0.6** — the figure
`docs/CORE-DIARY.md`'s correction already named as the fix's expected cost. Generation-time
cost is bounded exactly as D-39's was: a target that already needed a readability or
headword-initial retry now sometimes carries a third feedback section in the same call,
and a target failing only the new check adds one retry to an `(owner, field)` that
previously made none. Nothing here touches `data/core-store`: `enrich.py`'s check is
exercised by a new marker headword (`ABSENT_HEADWORD` / `HEADWORD_ABSENT_FEEDBACK_MARKER`)
in `tests/conftest.py`, and `example_hygiene.py` is exercised entirely by
`tests/test_example_hygiene.py`'s offline `scripted_model` against `tmp_path` stores.

## D-46 (2026-09-02) — A `.general` domain verdict is weak only if it predates the taxonomy

**Context.** Hygiene step (d) cleared every `.general` tag on every sweep so
`tag_domain` would retry it; on the core that re-billed ~11.5K senses per run (~$0.6)
and mostly produced the same `.general` verdict again. With taxonomy v2 (D-44) the
leaf set changes, so a retag is warranted exactly once.

**Decision.** `tag_domain` stamps `taxonomy_version=<TAXONOMY_VERSION>` on its
provenance note. Step (d) clears a `.general` tag only when the entry carries no
`tag_domain` verdict at the current version (or legacy-mapped, as before).

**Consequence.** Bumping `TAXONOMY_VERSION` triggers one retag of `.general` senses
store-wide; repeated sweeps are $0 on them. Verdicts without a version note (pre-D-46)
count as stale, so the first sweep after this change retags them once.

## D-47 (2026-09-02) — The prose-rewriting pass runs before the form-checking one, and "already tried" is per offending set

**Context.** Two retrofit passes read stored renditions. `rendition_hygiene` (D-39)
rewrites gloss renditions that open with their own headword; `readability_hygiene` (D-42)
rewrites renditions that still miss their reading band. D-42 put the second one *last*, on
the argument that the more expensive pass should not spend fixing text the other was about
to rewrite. Measured on the 10K core (iteration 7): headword-initial gloss renditions went
from 4,546 **up** to 6,480 (2.23% of 290,624). The two mechanisms behind it compound.
First, the simplest way to say a hard definition is the way a dictionary must not: asked
only to lower a grade, the model answers "A ban is an order to stop.", and running that
pass last means nothing checks its output. Second, `rendition_hygiene`'s marker was a
per-entry boolean (`rendition_hygiene:rewritten`), so every entry it had already stamped
was skipped forever — including the ones the later pass had just spoiled.

**Decision.** Three changes, all in `workflows/retrofit.py`.

1. **Order.** `RetrofitPass.ALL` becomes `(classify_kind, hygiene, tag_domain, spans,
   repair, readability_hygiene, rendition_hygiene)`. The rule is not "cheap before
   expensive" but **the pass that rewrites prose runs before the pass that checks the form
   of stored prose**, so a rewrite that opens with the headword is caught in the sweep that
   produced it. `rendition_hygiene` is now last of all.
2. **The rewriting pass is held to the rule it was breaking.**
   `READABILITY_HYGIENE_INSTRUCTIONS` gains `RENDITIONS_INSTRUCTIONS`' own sentence for it
   ("Never begin a definition rendition with the headword, with \"the word X\", or with
   \"X is\"; …"), sliced out at import time by the same `_extract_instructions_block` that
   already lifts that prompt's reading-level and field-meaning blocks — never restated, so
   the two cannot drift. Instructions are not enough on their own (that is the whole
   premise of `hygiene.py`), so acceptance enforces it too: a gloss rewrite that
   `is_headword_initial` is refused exactly the way one that reads no easier is — old text
   kept, `OG_READABILITY_MISS` kept, `readability_rewrite_rejected_headword_initial`
   logged. Only glosses, and only non-proper-nouns: an example sentence may open with its
   headword, and a proper noun's definition legitimately names its entity (D-30).
3. **"Already tried" means "already tried *this set*".** Both passes' note sentinels become
   `<pass>:<digest>;attempts=<n>`, where `<digest>` is 16 hex characters of SHA-256 over
   the sorted rendition ids the pass was answering for on that sweep (`_offender_digest`,
   `_hygiene_attempt_due`, shared by both). An entry is skipped only when what offends
   *now* hashes to what its most recent marker was written for; a changed set — a new
   offender, or one fewer — earns one more attempt, on the current offenders only. The
   attempt count in the same note bounds that at **two per entry**, after which what still
   offends is left flagged rather than billed a third time. The bound is per entry rather
   than per rendition on purpose: a second sentinel record per rendition id would be
   exact, but the note already has to carry the digest, and one integer beside it is the
   whole of the bookkeeping this way. Example renditions have no unique keyed id (several
   may share one `(level, register)`), so `readability_hygiene`'s digest includes an
   example's position in its sense's list.

**Consequence.** Every core entry stamped with the old boolean carries
`<pass>:rewritten`, which no digest can equal, so the first sweep after this change gives
each of them exactly one more attempt on whatever offends now and then stops — which is
the remediation for the measured 6,480, at ~6.5K entries × one nano call ≈ **$1.8**, not a
new standing cost. Steady state is unchanged: an entry whose offending set is stable costs
$0 on every later sweep, and an entry with nothing to fix was never billed at all. Nothing
here touches `data/core-store`. Tests: 550 → 556 — the two ordering assertions inverted,
plus a refused headword-initial gloss rewrite (with the example rewrite from the same
answer still adopted), a re-attempt after a new offender appears, no re-bill when the set
is unchanged, the two-attempt bound, and the marker format itself including the
pre-D-47 note. `tests/conftest.py` gains one marker headword
(`READABILITY_INITIAL_HEADWORD`, whose scripted rewrite is simpler but headword-initial),
and `READABILITY_FIX_TEMPLATE` no longer opens with its headword, since under the new rule
that fixture would have been refused rather than adopted.

## D-48 (2026-09-02) — The QA judge: a second model's opinion is data, not an edit

**Context.** Every check in the pipeline until now has been deterministic: a
Flesch-Kincaid score, a headword-present test, a slug lookup, a cycle in a graph. Those
catch form, and the close-out audit (`docs/CORE-DIARY.md`) shows the store is essentially
clean on all of them — 100% rendition coverage, 0 hypernym cycles, 0 artifact relations,
36 headword-initial canonicals. None of them can say whether a gloss is *true*, whether
two senses are actually distinct, or whether an example illustrates the sense it is filed
under. `Assessment.qa_score`, `judge_model` and `judged_at` have been on the schema since
v3 and were null on all 10,000 core entries: nothing had ever written them.

**Decision.** A new `workflows/qa.py` judges finished entries with `StageName.QA` —
`claude-opus-5`, the only stage on a different provider from the generator, because a
model marking its own homework agrees with itself. Six choices are load-bearing.

1. **The verdict is a rubric, not a review.** `contracts.DraftQAVerdict` asks for an
   `entry_score` and, per sense, six independent booleans (`gloss_accurate`,
   `distinct_from_other_senses`, `examples_natural`, `examples_fit_sense`,
   `relations_valid`, `domain_fits`) plus, per sampled rendition, three (`faithful`,
   `level_appropriate`, `register_appropriate`). Booleans are what makes a defect *rate*
   meaningful — every judged sense answers every dimension, so the denominator is real —
   and they are what a later pass can select on. The free-text fields are capped at 120
   characters and are null on a pass: a judge given room to narrate spends its output
   budget narrating, which is exactly what `RESOLVE_INSTRUCTIONS` had to be rewritten to
   stop (D-38).
2. **`flags` is the closed `QAFlag` enum, passed as the allowed set.** Structured output
   makes an out-of-vocabulary flag impossible, which is the whole argument for adopting
   an MQM-grounded closed list (`docs/STANDARDS.md` § 9d) rather than free text.
3. **The judge never writes the two flags the priced passes own.**
   `og.readability_miss` selects `readability_hygiene`'s offenders and `og.headword_absent`
   is what `example_hygiene` clears; a judge writing either would silently enqueue
   model-priced rewrites on the strength of an opinion, and the two mechanisms would stop
   being auditable against each other. The judge's view of the same two properties lands
   on `audience_inappropriate` (MQM Audience appropriateness) and `off_topic` instead. The
   sense- and rendition-level mappings are otherwise straight out of § 9b: an inaccurate
   gloss is `factual_error` (Accuracy > Mistranslation), a conflated pair is
   `missing_content` (Accuracy > Omission) plus `og.duplicate_gloss` — the on-disk
   condition a dedupe pass looks for, which the judge can see and a string comparison
   cannot — unnatural examples are `awkward_style` (Style), and an invalid relation or a
   wrong domain is `terminology_error` (Terminology > Wrong term / Inconsistent use).
4. **A sampled entry, not a whole one.** The prompt shows at most 8 senses, and per sense
   three gloss renditions (grade_1/plain, college/plain, neutral/technical), one example
   rendition (grade_1/plain), plus the two encyclopedia openings — 34 renditions at the
   worst case, which is exactly `QA_MAX_RENDITIONS`, so no rendition is ever shown without
   room for a verdict about it. Long texts truncate at 120 words with a visible ellipsis,
   so the judge does not read a truncation as missing content. Showing all nine gloss
   renditions of every sense would triple the prompt for a signal the defect rates already
   carry.
5. **`NativeOutput(strict=True)` works on Anthropic through the existing `StageRunner`,
   unchanged.** Verified live 2026-09-02: `AnthropicModel('claude-opus-5').profile` reports
   `supports_json_schema_output=True`, and both a one-field smoke contract and the real
   `DraftQAVerdict` came back valid on the first attempt. `router.settings_for`'s Anthropic
   branch already emits a bare `ModelSettings(max_tokens, timeout)` and the QA policy sets
   `temperature=None` and `reasoning_effort=None`, which matters: the profile reports
   `anthropic_disallows_sampling_settings=True`, so a policy that set a temperature would
   have failed at the provider.
6. **The sample is stratified and deterministic.** `stratified_sample(store, core_words, n,
   seed)` strata are `(kind, sense-count bucket 1 / 2-3 / 4+, frequency tercile from the
   rank in `core_10k.tsv`)`, every non-empty stratum gets a slot before proportionality is
   applied, and the draw is seeded per stratum. The same `(list, n, seed)` always yields
   the same ids, which is the only thing that makes iteration N's numbers comparable with
   iteration N+1's.

**Consequence — the cost is an order of magnitude above the other stages, and it was
measured, not assumed.** The first live call (`vow`, 3 senses, 14 sampled renditions) cost
**$0.12286**: 8,022 input tokens and 3,310 output. The input is three roughly equal parts —
the ~2.4K-token static rubric, the ~1.6K-token entry, and the ~1.5K-token JSON schema the
native structured output sends on every call — and the output is one record per sense plus
one per sampled rendition, so it is nothing like the few hundred tokens a `resolve` answer
costs. `cli.py`'s `qa --dry-run` estimate was written at a pre-measurement guess of
4,000/900 and is corrected here to 8,000/3,300; the guess under-priced a sweep by a factor
of three. At the measured rate a 50-entry sample is ≈ **$6.1** and judging the whole 10K
core would be ≈ **$1,200** — so QA is a sampling instrument, not a sweep, and
`run_qa` is idempotent on `judge_model + judged_at` so a re-run over an already-judged
sample costs $0 unless `--force`.

**Known residual: no prompt caching on this path.** `settings_for`'s Anthropic branch sets
no `cache_control` breakpoint, so the ~3.9K tokens of rubric-plus-schema that are identical
on every call are re-billed at the full input rate (`cached_input_tokens=0`, confirmed
live). At Opus rates that is roughly $0.02 per entry, ~16% of the call. Fixing it is a
`router.py` change and is deliberately out of scope here; the instructions are already
byte-stable and comfortably over the 1,024-token minimum a cache needs, so the fix is one
setting, not a prompt rewrite.

**Nothing here touches `data/core-store`.** The live check ran against a copy of one entry
in a temp store. Tests: 556 → 581 — the contract against the scripted payload, the prompt's
two caps, the whole verdict-to-`Assessment` mapping (entry score, per-sense proportional
score and flags, per-rendition flags, the clean-verdict case that writes nothing), the
refusal to write the two pass-owned flags, retired senses excluded, idempotence and
`--force`, sample determinism and full stratum coverage, metrics aggregation on a
hand-built outcome, and the CLI's dry-run estimate and report file. `tests/conftest.py`
gains one `_payload_for` branch whose scripted verdict carries exactly one defect of each
shape.

## D-49 (2026-09-02) — `content_hygiene`: six content defects, three settled by rule and three by one call per entry

**Context.** A QA/QC scan of the 10K core store (2026-09-02) counted six defects that no
existing pass looks for, because none of them is a property of the graph's *shape* —
which is what `audit.py` measures and `graph_hygiene.py` (D-43) repairs — or of a
rendition's *form*, which is what `retrofit.py`'s two rendition passes (D-42, D-47)
check. They are properties of the content itself:

| Defect | Count | Shape |
|---|---|---|
| a sense asserting both `synonym` and `hypernym` at one target | 9,873 | 7,527 with both sides resolved |
| a sense naming its own lexeme a synonym | 185 | v1.3 import, no provenance note |
| a sense asserting both `synonym` and `antonym` at one target | 63 | several are `graph_hygiene` reciprocals of a wrong far-side edge |
| a canonical example in a stilted academic register | 5,401 | matches `\b(researchers?|participants?|observers?|the study|this study|data ?set)\b` |
| two rendition targets carrying identical text | 592 senses | e.g. `fatigue:verb:0` formal == informal |
| a non-canonical rendition identical to the canonical gloss | 87 | |
| an example that is not a sentence | 21 | `hypernyms([`, `?`, bare single words |

The synonym/hypernym pairs are the interesting ones, and the reason this could not be a
rule: the direction is genuinely mixed. `tahoe:noun:0` asserts both at `lake` and should
assert neither of them — it is an *instance* of a lake; `teach:verb:0` and `instruct`
really are synonyms and the hypernym is the wrong edge; `chief:noun:2` and `title` are
hypernym and the synonym is the wrong edge. A rule that always kept one of the two would
be wrong about a third of the time in each direction.

**Decision.** A new `workflows/content_hygiene.py` with six steps selectable by name
through `run_content_hygiene(..., only=...)`, each idempotent, each a pooled sweep whose
handler holds the entry's lock across read → work → call → write (D-31). Nothing is
deleted anywhere except step 4, and there the deleted text goes into a zero-cost
`Provenance.note` first.

1. `self_synonym` (free) — demote to `see_also`, note `demoted: self-synonym`.
2. `synonym_antonym` (free) — demote the **antonym**, note `demoted: contradicts synonym`.
   A second pooled phase visits the *far side* under that entry's own lock and demotes
   the assertion pointing back, but only when it actually names the demoted sense (by
   resolved `sense_id`, or by `graph_hygiene`'s own `reciprocal of <sense id>` note): a
   far-side antonym about a different sense is not the copy and survives.
3. `synonym_hypernym` — **split by kind.** A proper noun is decided by rule, for free: a
   named entity is never a synonym of its category, and its relation to that category is
   WN-LMF's `instance_hypernym`, so the hypernym is retyped to `instance_of` (`retyped:
   proper noun instance`) and the synonym demoted. Everything else costs one nano call
   per entry on the `HYGIENE` policy — a structural verdict about two definitions, not
   prose — listing each pair as its source gloss, the target term and the *target's own*
   canonical gloss (`(unresolved)` where the relation was never resolved), answering
   `synonym | hypernym | neither`. The loser is demoted with the note `demoted: nano
   chose <keep>`; `neither` demotes both.
4. `garbage_examples` (free) — an example rendition of fewer than three words or with no
   ASCII letter is removed, its text kept in a note `removed garbage example: <text>`.
   A sense left with no canonical example is **not** repaired here: `retrofit`'s `repair`
   pass step (b) already writes exactly that, so this step does not duplicate it and does
   not call it. Run `retrofit --only repair` after.
5. `stilted_examples` — **canonical `(neutral, plain)` examples only.** A `college`
   rendition using the same words is doing its job; rewriting it would be the defect. One
   luna call per entry on the `RENDITIONS` policy asks for a natural everyday sentence for
   the same sense; a rewrite is adopted only if `find_span` can place the headword in it
   (morphology forms *and* `generate_forms`), and its span and Flesch-Kincaid grade are
   re-measured from what is actually stored.
6. `degenerate_renditions` — per sense, the second and later members of any group of
   non-canonical gloss renditions sharing one normalised text, plus any non-canonical
   rendition that is a copy of the canonical. One luna call per entry; a rewrite is
   accepted only if it is markdown-free, is not `is_headword_initial` (proper nouns
   exempt, D-30), and differs from the canonical and from every sibling **as the set
   stands at that moment**, which is what lets several offenders on one sense be applied
   one after another without the second recreating the first's duplicate.

The three free steps are idempotent because they leave nothing for themselves to find.
The three model steps carry D-47's marker — `<prefix>:<digest>;attempts=<n>` on the
`note` of their own call record, the digest over the set of refs the call answered for —
rather than a per-entry boolean, for D-47's reason: a boolean makes "already tried this
entry" permanent even after another pass changes what offends. Sentinels rather than a
bare `StageName` because all three reuse a shared stage's policy rather than adding one,
so the stage alone would collide with `hygiene`, `repair`, `rendition_hygiene`,
`readability_hygiene` and `example_hygiene`, which all do the same.

Instructions and contracts are module-private, and the parts of them that restate an
existing rule are **sliced** out of `prompts.RENDITIONS_INSTRUCTIONS` at import time by
the same `_extract_instructions_block` technique D-42/D-47 use — the `examples` field
paragraph for step 5, the reading-level, register and headword-initial blocks for step 6 —
so a rewrite is held to the exact bar the original generation was held to and the two
cannot drift. `contracts.py`, `prompts.py` and `cli.py` are being edited concurrently on
this branch, which is the second reason nothing here lands in them.

**Consequence.** Expected core spend, one sweep: step 3 ≈ 7K nano calls ≈ **$0.5** (the
~1,043 proper nouns among them are free); step 5 ≈ 3,500 luna calls ≈ **$1.5**; step 6 ≈
600 luna calls ≈ **$0.2**; steps 1, 2 and 4 are **$0** and settle 269 relations and
examples between them. Roughly **$2.2 one-off**, not a standing cost: an entry whose
offending set is stable costs $0 on every later sweep, and an entry with nothing to fix
was never billed at all. Wiring `retrofit --only content_hygiene` into a `RetrofitPass`
member is left to whoever next touches `retrofit.py`/`cli.py`; `run_content_hygiene` is
written to be callable exactly the way that wiring will need it. Nothing here touches
`data/core-store`. Tests: +38 (`tests/test_content_hygiene.py`), and `tests/conftest.py`
gains three scripted payloads in an append-only block at the end of the file — the
relation verdict is a function of the target term listed in each row, so one call scripts
all three answers, and two marker headwords script the two rejection paths step 6 has
(`DEGENERATE_INITIAL_HEADWORD`, `DEGENERATE_ECHO_HEADWORD`); step 5's rejection path
reuses the existing `NO_SPAN_HEADWORD`.

**Amendment (2026-09-02) — a seventh step, `fragment_examples`, for the sentence
fragment the QA judge's after-sample found (`docs/QA-DIARY.md`, iteration 3).** Measured
against every canonical example on the 10K core: a broad academic-register regex built
from the judge's own language matched 2,641, but most of those read fine on inspection
("Students categorize posts with a hashtag.") — a wider `STILTED_RE` would not be a
better one. The one class in that sample that *is* defective by construction, and
countable without judgment, is the sentence fragment: 498 canonical examples start with
a lowercase letter, carry no terminal punctuation, or both — "the mile-long bridge
opened to traffic" is missing both the capital and the stop. Capitalising the first
letter and appending a period is not proposed as the fix, deliberately: several of the
498 are genuinely incomplete thoughts rather than well-formed sentences missing their
edges, so a rule would produce a grammatical-looking fragment rather than a sentence.

`fragment_examples` runs the same shape as step 5, immediately after it in
`ContentHygieneStep.ALL` (so it judges each entry's canonical examples as `stilted_examples`
left them, catching a stilted rewrite that happens to still read as a fragment): one luna
call per entry on the `RENDITIONS` policy, reusing step 5's own `_EXAMPLE_FIELD_RULE`
slice of `RENDITIONS_INSTRUCTIONS` rather than cutting a second one of the same text, asking
for one complete sentence per offender. A rewrite is adopted only when it starts with an
uppercase letter, ends in one of `.`, `!`, `?`, `"`, `”` or `)` (a closing quote or
parenthesis ends a sentence as surely as a stop does), and `find_span` can still place the
headword in it — the same non-negotiable every rewriting step in this module holds to. The
old text is kept in a zero-cost note reading `superseded fragment example: <text>`.
Detection strips a leading quote mark before the lowercase check (a sentence quoted whole
still opens on its own capital one character in — `'"Go!" she said.'` is not an offender)
and touches nothing at the trailing end, since a trailing quote or paren is already an
accepted terminator in its own right.

**Consequence.** ~450 of the 498 measured fragments are canonical examples no other step
already rewrites (a handful overlap `stilted_examples`'s 5,401 and are settled there
first); one luna call each, ≈ **$0.15** one-off on the core, the same non-standing-cost
shape as steps 5 and 6. Tests: +13 (`tests/test_content_hygiene.py`); `tests/conftest.py`
gains one scripted payload in a new append-only block at the end of the file (a further
addition rather than an edit to the existing content_hygiene block, for the same
concurrent-editing reason that block gives) — one marker headword scripts a reply that is
still a fragment, and the existing `NO_SPAN_HEADWORD` is reused for the headword-lost
rejection.

## D-50 (2026-09-02) — `relation_hygiene`: the graph is consistent and untrue; three rules and one verdict per relation

**Context.** The QA judge's first sample (D-48; `docs/QA-DIARY.md`, Iteration 1 —
`claude-opus-5`, 58 core entries, 179 senses, $4.74) returned the worst number in the
resource: `relations_valid` false on **92.7%** of judged senses, and **44.9%** of the
1,734 individual relations on those entries named invalid — antonym 51%, hyponym 51%,
synonym 40%, hypernym 35%. D-43's `graph_hygiene` made the graph *consistent* (no
self-loops, no cycles, symmetric types reciprocated) and D-49's `content_hygiene` settled
the type pairs that *contradict* each other; neither of them ever asks whether the thing
on the far end of an edge is what the type says it is.

The judge listed its offending targets, and they are not a smear of borderline judgement
calls. Four shapes account for nearly all of them, and three are decidable by rule:

| Shape | Judge's examples | Decidable |
|---|---|---|
| an inflected form of the headword | `stay` synonym "stays" | by rule |
| an inflected form of a *sibling target of the same type* | `banner` synonym "banners" beside "banner"; `ad` synonym "ads", "advertisements" | by rule |
| a modifier phrase built on the headword | `benjamin` hyponym "crisp benjamin", "counterfeit benjamin", "folded benjamin" | by rule |
| a meta-label | "slang term", "modifier", "biblical name", "popular given name" | by rule |
| a true claim under the wrong type | `benjamin` **antonym** "one-dollar bill" — another banknote, not an opposite | needs a model |
| a descriptive phrase that is not a lexical unit | "indoor plant", "ornamental tree", "secondary account" | needs a model |

**Decision.** A new `workflows/relation_hygiene.py` with four steps selectable by name
through `run_relation_hygiene(..., only=...)`, each idempotent, each a pooled sweep whose
handler holds the entry's lock across read → work → call → write (D-31). **Nothing is
deleted anywhere**: a relation that fails a check is demoted to `see_also`, which still
says something true, or retyped to the type that would have been true, and the reason
goes on `Relation.note`.

1. `inflections` (free) — a target matching the headword's inflected forms (the
   `Morphology` block ∪ `spans.generate_forms`, the same union `content_hygiene._forms_for`
   builds for span finding) is demoted with `demoted: inflection of headword`; a target
   that `generate_forms` produces from **another target of the same type on the same
   sense** is demoted with `demoted: inflection of sibling <term>`, the shorter (base)
   form kept. `derivation` relations are exempt from both: morphology is that type's
   subject, and demoting one would delete the information it exists to carry.
2. `headword_phrases` (free) — a *multi-word* target containing the headword as a whole
   word is a description of the headword, not a second lexical unit: `demoted: modifier
   phrase on headword`. The exception is decided by the **store, not by `kind`**: if the
   target is itself an entry (`store.exists`) it is a real compound ("ice axe" under
   *ice*) and is kept. That is a fact about the target, which is what the question is
   about, and it is more accurate than asking whether the *source* is a
   `compound`/`idiom`/`phrasal_verb`. `collocation` and `used_with` are exempt — "a
   solemn vow" *is* the collocation of *vow*, and it is the one correct instance of the
   shape this step otherwise rejects.
3. `meta_labels` (free) — `demoted: meta-label`. `META_LABELS` mirrors `filters.py`'s own
   list (module-private there, so mirrored rather than imported) and extends it with the
   lexicographic labels the judge found; `META_QUALIFIERS` catches the open-ended
   `<qualifier> term|form|name` shape as a *qualifier* list rather than a bare suffix
   rule, so "life form", "art form" and "code name" — real lexical units — survive.
   Targets are compared through `filters.normalise_candidate`, so the frontier filter and
   this pass judge a surface form the same way.
4. `validity` — one nano call per entry on the `HYGIENE` policy, chunked at
   `MAX_REFS_PER_CALL = 60` refs, listing every relation the free steps left standing as
   its type, its sense's gloss (**once per sense**, not once per relation), the target
   term and the target's own canonical gloss where resolved (`(unresolved)` otherwise).
   `RELATION_VALIDITY_INSTRUCTIONS` is byte-stable and ~1.8K tokens: it defines **every**
   `RelationType`, because the measured defect is very often a true claim under the wrong
   type and a model cannot propose a type it was never told the meaning of, and it carries
   one worked example covering a plural artifact, a modifier phrase, a wrong-type antonym
   and two valid relations. The contract is
   `{verdicts:[{ref, valid: bool, better_type: str|null}]}`. `valid` keeps the relation;
   not `valid` with a `better_type` naming a *different* real type **retypes** it
   (`retyped: nano <old>→<new>`); anything else demotes it (`demoted: nano invalid`). A
   `better_type` on a relation called valid is ignored — a retype is a repair and there is
   nothing to repair. `see_also` relations are **not listed at all**: that type is this
   pass's floor, the only verdict that could change one is a promotion, and it is both the
   largest and the least consequential slice of the graph.

**The marker's one deviation from D-47/D-49.** The sentinel is D-47's shape —
`relation_hygiene:validity:<digest>;attempts=<n>`, bounded at two attempts per entry — but
the digest is taken over the ref set **as it stands after the verdicts have been
applied**, not before, and the ref id is read live off the relation (`<sense>|<type>|<target
lexeme>`) rather than frozen at collection time. Taken `content_hygiene`'s way, a sweep
that demoted or retyped anything would leave a marker describing a set that no longer
exists, and the very next sweep would find a different digest and buy a second opinion
about the relations the first sweep had already passed. Taken this way the marker reads
"I have judged exactly this set", a second sweep over an unchanged entry is free, and an
entry that later *gains* a relation still earns its one further attempt. Because the
marker is written once after all of an entry's chunks, it lives on its own zero-cost
provenance record rather than on a call record.

**Run order, and `graph_hygiene`'s reciprocity step.** Run this pass **after**
`graph_hygiene`, and re-run it if `graph_hygiene` is re-run. Step 4 there adds the implied
reverse of a *symmetric* relation, and `see_also` is not one of `SYMMETRIC_RELATION_TYPES`
(`synonym`, `antonym`, `confusable_with`), so nothing is ever inferred *from* a relation
this pass demoted. But that step keys its "already asserted" set on the exact triple
`(lexeme, type, target)`: a demoted `synonym` A→B leaves `(A, synonym, B)` unasserted while
a **resolved** `synonym` B→A may still stand on the far side, and step 4 would then write
the forward edge back onto A as a fresh reciprocal. `content_hygiene`'s `synonym_antonym`
step answers this for its own demotions with a second pooled phase over the far entry;
this pass deliberately does not, because its demotions are overwhelmingly of targets that
are not entries at all ("banners", "crisp benjamin", "slang term") and so have no far side
to reciprocate from. The residual is `validity` demoting a *resolved* synonym or antonym;
the run-order rule is the mitigation and a far-side phase is the fix if a measured sweep
shows it matters. Recorded in the module docstring, not left implicit.

**Consequence.** Expected core spend, one sweep: the three free steps are **$0** and are
expected to take the largest single bite out of the 44.9% (the judge's own list is
dominated by the three rule-decidable shapes); `validity` is ≈ **10K nano calls ≈ $0.8**
for a 10K-entry store, one call for almost every entry and two for the few carrying more
than 60 relations. Not a standing cost: an entry whose relation set is stable costs $0 on
every later sweep, and an entry with nothing left to judge was never billed at all.
`StepResult` and `UNRESOLVED_GLOSS` are imported from `content_hygiene` — they are that
module's public surface and this pass reports the same shape — while its tally, pool
driver, provenance helpers and marker are module-private there and are mirrored. This
module's own contract and instructions stay module-private for D-49's reason:
`contracts.py`, `prompts.py`, `readability.py` and `enrich.py` are being edited
concurrently on this branch. Wiring `retrofit --only relation_hygiene` into a
`RetrofitPass` member is left to whoever next touches `retrofit.py`. Nothing here touches
`data/core-store`. CLI: `opengloss relation-hygiene [--only ...]`, mirroring
`content-hygiene`. Tests: +37 (`tests/test_relation_hygiene.py`), and `tests/conftest.py`
gains one scripted payload in a second append-only block at the end of the file — the
verdict is a function of the target term listed in each row, so one call scripts the
demotion path (`RELATION_INVALID_TARGET`), the retype path (`RELATION_RETYPE_TARGET`) and
the untouched path that makes idempotence testable.

**Amendment (2026-09-02) — the far-side phase the original text called optional turned out
not to be.** A measured sweep of `validity` over the 10K core found synonym reciprocity had
fallen from 99.96% to 78.7% and antonym from 99.99% to 84.7%, because every step here —
not only `validity` — regularly demotes a *resolved* `synonym`, `antonym` or
`confusable_with` relation, and every one of those has a far side that can now be stale.
Every step gained the same second pooled phase `content_hygiene`'s `synonym_antonym` step
already has: a near-side demotion of one of the three symmetric types toward a different
lexeme queues a check of that lexeme's own relations, run once the step's main sweep has
fully drained (D-31 — no two locks held at once), demoting every relation there of the
same type that points back at the source and either is unresolved or names the exact sense
that was demoted. The note is `demoted: far side of <sense id> (<reason>)`, which starts
with `"demoted:"` so `graph_hygiene`'s reciprocity step still refuses to write the pair
back. Counted separately as `far_side_demoted` on the step result and the outcome, folded
into `demoted` too so that figure still means "how many relations did this step demote".
Idempotent by construction, the same way every other check here is: a relation already
`see_also` no longer matches its pre-demotion type, so a sweep that demotes nothing queues
nothing. Tests: +5 (`tests/test_relation_hygiene.py`), no `conftest.py` change — the
`validity`-step case reuses the existing `RELATION_INVALID_TARGET` scripted payload.

**Second amendment (2026-09-02) — the far-side phase is not given the run's stop event.**
The amendment above shipped with its far-side phase driven through the same `_drive`
wrapper as the near-side one, and given the same `stop_event`. That is wrong for this one
phase, and it made the mechanism a no-op on the store it was written for: the real 10K
`validity` sweep reported `demoted=12321, retyped=2700, far_side_demoted=0`, and a
read-only scan afterwards found 6,840 entries targeted by a `demoted: nano invalid`
`see_also` with **4,468 far-side reciprocals still open**. `run_pool`'s workers check the
stop event *before pulling their first item* and return, and a budget stop sets that event
(`runner.run_pool`) — so a step that stops on its cap runs its second phase over zero
items, silently and with no warning of its own. The unit tests never saw it because none
of them stops a step; the same run's free `inflections` step, which reached its end with
the event still clear, reported its 6 far-side demotions correctly.

The fix is one line and its justification is the phase's economics. Every other pooled
sweep in this pass *buys* something, so a stop event is a "spend no more" instruction it
must honour. The far-side phase buys nothing: it is the repair of near-side demotions the
run has already paid for and written to disk, one local read-modify-write per target entry
and not a single model call, over a list bounded by the demotions the run actually made.
Honouring a stop there abandons the repair and leaves the store asserting exactly one half
of a pair the pass has just judged untrue — a worse outcome, on every axis, than the few
seconds of free I/O it saves. `_demote_far_side_all` therefore no longer takes a
`stop_event` parameter at all (rather than taking one and ignoring it, so no future caller
can reintroduce the bug by passing one), and drives with `stop_event=None`. The step's
`stopped_reason` is untouched: the near-side phase's `budget`/`stopped` still stands, so a
stopped step still reports that it stopped, having repaired what it did do.

Three other hypotheses were checked and are **not** in play. The requests are collected in
a list shared across the pool's handlers under their own lock, not in a per-handler or
per-chunk local, so nothing is dropped by chunking at `MAX_REFS_PER_CALL`. `_is_far_side_of`
accepts an unresolved far side as well as one naming the demoted sense, which is the shape
the scan counted as open. And a far side already demoted to `see_also` fails the type test
and is skipped, which is the idempotence the amendment above claims and not a leak. The
deliberate exclusion — a far side resolved to a *different* sense of the source lexeme — is
left exactly as it was: demoting it would be a new defect, not a repair, and no measurement
yet says how many of those exist. If one ever does, the answer is a separate
`far_side_ambiguous` counter, not a widening of this test. `content_hygiene`'s
`synonym_antonym` second phase has the same shape and the same defect; it is not touched
here (another editor holds that file on this branch) and is noted for whoever does.
Tests: +2 (`tests/test_relation_hygiene.py`), both reproducing the real store's shape — a
sweep stopped part way with reciprocals both resolved to the demoted sense and never
resolved, once for a budget stop and once for a caller's stop mid-sweep.

## D-51 (2026-09-02) — Word familiarity is measured beside Flesch-Kincaid, generation-time and on disk

**Context.** The `claude-opus-5` judge sample of iteration 1 (`docs/QA-DIARY.md`) found the
largest single defect rate in the resource outside the graph: **46.6% of grade_1
encyclopedia renditions were not level-appropriate, and every one of them passes its
Flesch-Kincaid band.** Grade-1 glosses failed at 10.6% and grade-1 examples at 7.8% for
the same reason. FK is `0.39 x words/sentence + 11.8 x syllables/word - 15.59`: it
measures how long the sentences are and how many syllables the words have, and it cannot
see whether the reader *knows* the words. "Ancient people in Mesopotamia, Greece, and Rome
used oaths." and "Monks made vows of poverty, chastity, and obedience." are eight- and
nine-word sentences of one- and two-syllable words — exactly what the formula rewards.
Every deterministic check the pipeline had (D-39's headword-initial, D-42's readability
band, D-45's headword-absent) is blind to it, and `readability_hygiene` is blind to it by
construction: the offenders are already inside the band that pass selects on.

**Decision.** Add the other half of the classic pair — Dale-Chall's own formula is a
sentence-length term plus precisely this share — as a second deterministic metric, wired
into the same three places FK is.

1. **The word list.** `src/opengloss_generator/data/easy_words.txt`: the **Dale-Chall
   familiar-word list**, ~3,000 words a fourth-grade reader recognises, taken from
   textstat's mirror
   (`https://raw.githubusercontent.com/textstat/textstat/main/textstat/resources/en/easy_words.txt`,
   MIT, Copyright (c) 2016 Shivam Bansal; the list itself is Dale & Chall 1948, revised
   1995, freely usable and mirrored by every open readability library). Normalised once,
   at vendoring time and recorded in the file's own header: lowercased, trailing full stop
   dropped (`mr.` -> `mr`), hyphenated entries kept whole and also split into parts, sorted,
   de-duplicated — 2,947 entries. It is *package data*: `data/` is gitignored at the repo
   root (the entry store lives there) and hatchling honours VCS ignore files, so
   `.gitignore` gains one negation and `[tool.hatch.build.targets.wheel]` an `artifacts`
   entry, without which the file is silently absent from the wheel.
2. **`vocabulary.py`.** `hard_word_share(text, *, ignore=())` is the share of a text's
   alphabetic tokens that are not on the list; `hard_words(...)` returns them, in order and
   without repeats, which is what makes the model feedback actionable; `vocabulary_band(level)`
   is the ceiling — **grade_1 0.10, grade_5 0.25, everything else `None`**, because a
   grade_10 or college reader is expected to meet words they do not know and a `neutral`
   rendition has no audience to fail. Three kinds of token are not counted at all: the
   headword and its forms (a definition cannot avoid its own headword), proper nouns (taken
   to be tokens capitalised anywhere but at the start of a sentence — Dale-Chall's own
   rules treat them as familiar, and a rewrite cannot remove "Mesopotamia" from a passage
   about Mesopotamia), and numbers, symbols and lone letters. An inflection of a familiar
   word is familiar, via a suffix-stripping `lemma_candidates` that is the cheap inverse of
   `spans.generate_forms`. Its failure modes are documented on the module and pinned in
   `tests/test_vocabulary.py`, the sharpest being that the 1948 list has no entry for
   "serious", "problem" or "area" — which is what `vocabulary_tolerance` exists to absorb.
   `readability.py` re-exports the three entry points so a caller asking "how hard is this
   text" gets both halves from one import.
3. **Generation time (`workflows/enrich.py`).** Exactly D-39's and D-45's shape applied to
   a fourth check, and **no `RENDITIONS_INSTRUCTIONS`/`PROMPT_VERSION` change**: the
   instructions already say "Only very common words: words a six-year-old already says out
   loud", and only enforcement is new. `_Measured` gains `hard_share`, `hard_terms` and
   `over_vocabulary`; the share is measured on **every** rendition at every level, because
   it costs one pass over a short text and it is the only familiarity signal the resource
   has, and is stored on `Assessment.hard_word_share` (new, `float | None`). At `grade_1`
   and `grade_5` a share over its band plus `ReadabilityConfig.vocabulary_tolerance` (new,
   default **0.05**) is a miss that joins the existing single combined retry, with a
   feedback section from the new `prompts.build_vocabulary_feedback(level, words)` that
   **names the offending words** — told only "use simpler words" a model shortens sentences
   that are already short. `_is_better` gains a tier below the two headword defects and
   above the grade: in band beats out of band, and lower share beats higher, because the
   judge's evidence is that this is what decides whether a grade_1 passage reads as
   grade_1. What is still over afterwards carries the new
   `QAFlag.OG_HARD_VOCABULARY = "og.hard_vocabulary"`, the sixth `og.`-prefixed member, and
   is added to the closed list `QA_INSTRUCTIONS` shows the judge as "detected
   deterministically, do not use". `ReadabilityConfig.vocabulary_check: bool = True` is
   independent of `enabled`, like its two siblings.
4. **What is already on disk (`workflows/vocabulary_hygiene.py`, new module).**
   `run_vocabulary_hygiene(store, runner, *, workers, stop_event=None, lexeme_ids=None)`
   visits every entry once and lists every offending `grade_1`/`grade_5` rendition — gloss,
   examples, encyclopedia and lexical explanation, since a hard word is a property of the
   text and not of the field — with its offending words, in one call per 3,000-word chunk
   on the `RENDITIONS` policy (luna, not `hygiene`'s nano: audience-held prose, not a
   structural verdict). A rewrite is verified, never trusted, against four bars: a strictly
   lower share, an FK grade still inside its band plus `tolerance` (so vocabulary cannot be
   traded for sentence length), not headword-initial for a gloss of a non-proper-noun
   (D-30/D-47's regression), and a findable headword form for an example (D-45). Whatever
   ends up stored has both metrics re-measured and both flags set or cleared to match, so
   neither lags behind its text; superseded text goes in a zero-cost `Provenance.note`;
   idempotence is D-47's offending-*set* digest marker with two attempts per entry. The
   module is self-contained for D-45's and D-49's reason — `retrofit.py` has concurrent
   work landing — at the cost of duplicating the instructions-slice helper and the marker
   parser; wiring `retrofit --only vocabulary_hygiene` is left to whoever next touches
   `retrofit.py`/`cli.py`.

**Consequence.** Measured read-only over **300 random core entries** (seed 51), share of
renditions above the bare band / above band + 0.05 tolerance: encyclopedia grade_1
**33.7% / 4.3%** (mean share 0.087), example grade_1 30.1% / 10.7%, gloss grade_1 15.1% /
6.0%, encyclopedia grade_5 26.7% / 7.0%, gloss grade_5 7.9% / 4.1%. The 33.7% figure is
the deterministic corroboration of the judge's 46.6%, from a metric that costs nothing.
**53.3% of entries carry at least one offender** at the acting threshold, 1.9 offenders
and ~62 words of source each — so a full core sweep of the retrofit pass is ≈ 5,300 luna
calls at ~$0.00023 each ≈ **$1.2**, and steady state after it is $0 by the marker.
Generation-time cost is bounded exactly as D-39's and D-45's were: a target already being
retried now sometimes carries a fourth feedback section in the same call, and only the
4-11% of grade_1/grade_5 targets that fail *only* this check add a retry that was not
being made. Tests: **+61** (`test_vocabulary.py` 30, `test_vocabulary_hygiene.py` 20,
`test_enrich.py` +11), with `tests/conftest.py` gaining a third append-only block: one
marker headword whose scripted text is short-sentenced *and* full of unfamiliar words —
the defect's exact signature — plus the three rewrite-refusal markers the pass needs. Two
existing `test_enrich.py` tests now switch the new check off explicitly: the scripted
default rendition text carries four unfamiliar words in twenty-five ("text", "rewrite",
"gloss" and the register's own name), which is genuinely over the grade_1 band, and both
tests are about a cost identity rather than about this check.

## D-52 (2026-09-02) — `sense_hygiene`: two senses that are one meaning, and examples filed under the wrong one

**Context.** The `claude-opus-5` judge sample of iteration 1 (D-48; `docs/QA-DIARY.md` — 58 core
entries, 179 senses, $4.74) returned two defect rates that no deterministic check in this project
can see at all:

| Judged dimension | Rate | What the judge saw |
|---|---|---|
| `examples_fit_sense` | **34.1%** | "Sense 2's examples are mostly non-religious (sibling, race, broken toy) and belong to sense 1"; verb-sense examples that use the noun |
| `distinct_from_other_senses` | **25.7%** | near-duplicate senses that exact-text dedup cannot see — two noun senses of *vow* differing only by the word "religious" |

`retrofit`'s duplicate-sense pass compares *normalised gloss text*, so "a solemn promise" and "a
solemn religious promise" are two different strings and both survive it; nothing at all compares
an example against the definitions it was **not** filed under. Both are per-sense-pair judgements,
which is why iteration 1 deferred them to iteration 3 rather than trying a rule.

**Decision.** A new `workflows/sense_hygiene.py`, two steps selectable by name through
`run_sense_hygiene(..., only=...)`, each idempotent, each a pooled sweep whose handler holds the
entry's lock across read → collect → call → apply → write (D-31). Both make **one nano call per
qualifying entry** on the `HYGIENE` policy: matching a definition against a definition, or a
sentence against a definition, is a structural verdict rather than prose for an audience. Neither
step reads any other entry — both questions are answered entirely from inside one entry.

1. `distinctness` — for an entry with **two or more live senses under one part of speech**, one
   call lists every live sense as `[ref, pos, canonical gloss, first example]` and the contract is
   `{duplicate_groups: [[ref, ref, ...], ...]}`, empty when they are all distinct.
   `DISTINCTNESS_INSTRUCTIONS` is byte-stable and ~1.2K tokens and sets the bar where WordNet sets
   it — **a sense is distinct when a learner would need a separate definition for it** — and then
   spends most of its length on what is *not* a distinction, because that is where the measured
   defect lives: domain colouring (religious, legal, sporting), register, specific-versus-generic
   phrasing, and breadth of wording alone. It carries a worked example of both answers: *vow*
   (solemn promise / solemn **religious** promise) as a true duplicate pair, and *bank* (river /
   institution) as a split that must survive. It also tells the model to be conservative, since a
   group costs a sense.

   Applying a group: **the lowest sense index wins** (D-1 — sense ids are positional and are never
   renumbered), and everything the survivor lacks is merged onto it *before* the others are
   retired — canonical examples it does not already hold (compared on normalised text), example
   renditions at each `(level, register)` it has none for, relations it does not already assert by
   `(type, target lexeme)`, and gloss renditions at each `(level, register)` it lacks. Merged
   content is **deep-copied**: the retired sense keeps everything it had, and two senses sharing
   one mutable `Example` would make an edit to either show up on both. The losers are marked
   `Sense.retired`; nothing is deleted. A `Sense` has no `note` field, so the reason goes on the
   entry's provenance table as a zero-cost record reading `retired sense <sid>: duplicate of
   <survivor sid>`. A group whose members span two parts of speech is **refused whole** — a noun
   sense and a verb sense are never one meaning — as is a group naming a ref that does not exist
   or a sense an earlier group in the same answer already retired.

2. `example_fit` — for an entry with **two or more live senses at all** (a looser gate than step
   1's, deliberately: the noun-use-under-a-verb-sense shape only becomes visible when both parts
   of speech are on the table), one call lists every live sense as `[sense_ref, pos, gloss]` and
   every **canonical** example across all of them as `[example_ref, filed_under sense_ref, text]`.
   Two lists numbered from one, as the QA prompt does it. The contract is
   `{placements: [{example_ref, best_sense_ref: int|null}]}`. `EXAMPLE_FIT_INSTRUCTIONS` is
   byte-stable and ~1.2K tokens: what an example is for, how to decide (the meaning the sentence
   actually uses, not its topic), that **the part of speech is part of the answer**, when the
   answer is "none", that the common answer is "where it already is", what not to do, and a worked
   example carrying all four answers.

   An example whose best sense is where it sits is left alone. One that belongs elsewhere is
   **moved with its level renditions** — the `(level, register)` siblings at the same position
   within their own key's members, because nothing in the schema links a rendition to the
   canonical it renders (several examples may share one key, so `Lexeme.rendition_ids` gives
   example renditions no id at all) and position within the key is the link the renditions
   workflow actually writes. Spans are re-found with `spans.find_span` against the destination's
   own inflected forms. A destination already holding `MAX_CANONICAL_EXAMPLES = 3` canonical
   examples has no room — three is what the generator writes and what `retrofit`'s repair restores
   — so the example is dropped from the source into a zero-cost note `moved-out example (no room):
   <text>` rather than piled onto a sense that has enough. A `null` answer removes it with
   `removed example (fits no sense): <text>`. Every removed text is written to a note before the
   rendition comes out.

   A sense left with **no canonical example** is *reported* as `senses_emptied`, never repaired
   here: `retrofit`'s `repair` pass step (b) already regenerates canonical examples for exactly
   that condition, so the docstring and the CLI help both say to run `retrofit --only repair`
   afterwards. This pass does not call it and does not duplicate it.

**Idempotence.** D-47's sentinel on a zero-cost provenance record, bounded at `MAX_ATTEMPTS = 2`
per entry, over the *sense set* for step 1 and the *canonical example set* for step 2 (a ref being
`<sense id>|<normalised text>`, so a moved example is a new ref under its new sense). Following
**D-50 rather than D-49**, the digest is taken over the set **as the answers leave it**, computed
by re-running the very same collector the next sweep will run: taken the other way, a sweep that
merged a duplicate or moved an example would leave a marker describing a set that no longer
exists, and the next sweep would buy a second opinion about senses it had already passed. A second
sweep over an unchanged entry is therefore free, and an entry that later gains a sense or an
example still earns its one further attempt. An entry with one live sense is never listed, never
called for, and costs $0 on every sweep.

**Run order.** After `content_hygiene`'s `garbage_examples` (there is no point paying a model to
decide where `'hypernyms(['` belongs) and **before** `retrofit --only repair` (which refills what
`example_fit` empties). `distinctness` runs before `example_fit` within the pass, so no call is
ever billed to place an example among senses that were about to be merged. Retiring a sense
renumbers nothing, so no downstream sense id moves and no edge is re-pointed; `Lexeme.edges`
already skips retired senses, so a retired duplicate leaves the projected graph on its own.

**Consequence.** Expected core spend, one sweep: roughly 6K of the 10K entries carry more than one
live sense, at two nano calls each — one per step — of the same shape and size as `relation_hygiene`'s
verdict call, so ≈ **12K calls ≈ $0.9**, and $0 in steady state by the marker. Not every one of
those entries is billed by both steps: step 1's gate (two senses under *one* part of speech) is
narrower than step 2's, and an entry with no canonical examples is never billed by step 2 at all.
`PROGRESS_EVERY` is imported from `content_hygiene` so every sweep reads the same in a run log;
the tally, the pool driver, the provenance helper and the D-47 marker are module-private there and
in `relation_hygiene`, so they are mirrored, and this module keeps its **own** `StepResult` rather
than importing that one, because its counters are its own — nothing here is demoted, retyped or
rewritten. Its contract and instructions stay module-private for D-49's and D-50's reason:
`contracts.py` and `prompts.py` are edited concurrently on this branch. Wiring `retrofit --only
sense_hygiene` into a `RetrofitPass` member is left to whoever next touches `retrofit.py`. Nothing
here touches `data/core-store`. CLI: `opengloss sense-hygiene [--only ...]`, mirroring
`content-hygiene`. Tests: **+28** (`tests/test_sense_hygiene.py`), with `tests/conftest.py` gaining
a fourth append-only block whose two payloads are functions of the *listed row* rather than of the
headword — a gloss carrying the duplicate marker is grouped, an example whose text says "belongs to
sense N" is placed there and one that says "fits no sense" is removed — so a single entry can
script a merge, a move, a removal, a cap and an untouched answer at once.

*Third amendment (2026-09-02).* The identical defect in `content_hygiene._synonym_antonym_step`'s far-side phase is fixed the same way: that phase now drives with `stop_event=None`.

## D-53 (2026-09-02) — `examples`: verified sense-disambiguated sentences, in volume, one call per entry

**Context.** Two of the QA judge's dimensions have never moved, across four iterations and every
repair pass aimed at them (`docs/QA-DIARY.md`):

| Judged dimension | Iteration 1 | Iteration 4 (unseen entries) |
|---|---|---|
| `examples_natural` | 29.6% failing | **33.3%** — worse |
| `examples_fit_sense` | 34.1% failing | 31.8% |

Iteration 3's stilted-example rewrite is *why* the first got worse: it "replaced one template
(`Researchers…`) with another the regex cannot see (definition-like sentences, near-duplicates
across senses)". Iteration 4 drew the conclusion this decision acts on — "rewriting by pattern
moves the pattern", and example naturalness "belongs to a future per-sense regeneration, not a
repair pass". Separately, the Tier 2 plan (`docs/CORE-DIARY.md`) ranks material by what a small
encoder trains on, and sense-centred contexts sit at the top of that list: an example sentence is
the only text this project produces that *uses* the word in a known meaning rather than talking
about it, and it is the only text that can be verified for cents.

**Decision.** A new `workflows/examples.py`: `run_examples` writes `config.examples.per_sense`
(default 8) fresh sentences for every live sense of an entry, keeps only what passes a
deterministic sieve, and is idempotent.

1. **One call per entry, not per sense.** The prompt lists every live sense as `[sense_ref, pos,
   canonical gloss, one existing example]` and asks for N sentences for each. That is the only way
   "this sentence must fit ONLY the sense it is filed under" can be *asked for* at generation time
   rather than measured afterwards — the model writes sense 2's sentences knowing what senses 1
   and 3 mean — and it gives the lowest input:output ratio of any stage here: measured live, a
   2,700-token prompt (of which 2,460 is the cached instruction prefix) buys 296-1,804 output
   tokens of sentences. `EXAMPLES_INSTRUCTIONS` is byte-stable at ~1,950 tokens with a worked
   `bank` example carrying five good answers and five bad ones with the reason for each.

2. **Targets are cycled, not crossed.** `ExamplesConfig.targets()` pairs each configured reading
   level with `plain` and each configured register with `neutral`, then cycles that list to fill
   `per_sense` — the default eight being `grade_1/5/10/college` plain plus `neutral` informal,
   formal, technical and slang. Deliberately unlike `RenditionRequest`, which crosses its two axes:
   a grade_1 technical example sentence is not a thing. `slang` replaces `marketing` on the
   register axis (`DEFAULT_EXAMPLE_REGISTERS`) because a slang sentence about a river bank is
   something a person says and a marketing one is an advertisement.

3. **Acceptance is deterministic, per sentence, and nothing is retried.** One sentence, opening
   capital, terminal punctuation; word count inside `[min_words, max_words]`, tightened to 10 at
   `grade_1` and 16 at `grade_5` (the numbers `RENDITIONS_INSTRUCTIONS` already states);
   `find_span` places the headword or one of its forms (D-45's defect, refused rather than
   flagged); not gloss-*shaped* — `^(the|a|an)? <headword>s? (is|are|means|refers)` — where
   `is_headword_initial` is deliberately **not** used, because an example may legitimately begin
   with its headword and D-39's rule is about definitions; Flesch-Kincaid inside its level's upper
   bound plus `readability.tolerance` (upper bound only: rejecting a sentence for reading easily
   would select for padding); the Dale-Chall unfamiliar-word share inside its band plus
   `vocabulary_tolerance` at the two levels that have one (D-51); not a near-duplicate on
   normalised text of what the sense holds or of what this call has already produced; and not
   sharing its first three words with a sentence already accepted in the same call. A rejected
   sentence is **counted by reason and dropped**. This reverses `enrich.py`'s single-retry
   discipline on purpose: there, one call produces one rendition per target and losing it loses
   the target; here one call produces dozens of interchangeable sentences, and the next entry's
   call buys more of them more cheaply than a retry buys back one. The `rejected_by_reason` map is
   the feedback loop instead — it is what a later prompt change is aimed at.

4. **The one check no rule can make.** "Fits only this sense" is invisible to every rule above and
   is the judge's largest example defect, so an entry with two or more live senses buys a second,
   cheap call on the shared `HYGIENE` policy (nano) listing the accepted sentences as `[ref, text]`
   and the senses as `[sense_ref, gloss]` and asking which sense each illustrates — **without**
   saying which one it was written for, so the answer is a judgement rather than an agreement.
   `SENSE_FIT_INSTRUCTIONS` is the same question `sense_hygiene`'s `example_fit` asks of stored
   examples, worded for freshly written ones, and was extended to ~1,365 tokens specifically to
   clear the provider's 1,024-token caching floor (D-38's lesson). A sentence whose answer is not
   its own sense is **dropped, not moved**: it was written to illustrate a sense it does not fit,
   and the sense it does fit already has eight sentences written for it deliberately.

5. **Idempotence** is a sentinel on the generation call's own provenance record,
   `examples:<digest of live sense ids>;n=<per_sense>`. A rerun over an unchanged entry costs $0;
   an entry that gains or retires a sense, or a run configured for a different `per_sense`, earns
   exactly one more call. No attempt counter, unlike D-47's markers: this pass is filling a set,
   not repairing a defect that might survive an attempt.

**Measured live** (`river`, `argue`, `bank` — 1, 2 and 7 live senses — copied out of `data/core-store`
into a temporary store; nothing was written back):

| | sentences | accepted | rejected | dropped by the sense check | cost |
|---|---|---|---|---|---|
| `river` | 8 | 7 | 1 too_short | — (single sense, no check call) | $0.000204 |
| `argue` | 16 | 15 | 1 hard_vocabulary | 0 | $0.001038 |
| `bank` | 56 | 52 | 1 too_short, 1 headword_absent, 1 repeated_opening | 1 | $0.002136 |

**$0.00338 for 74 verified sentences: $0.0011 per entry and $0.000046 per accepted sentence.** A
full core sweep (10,000 entries, 34,015 live senses) is therefore ≈ **$11 for ≈ 272,000
sense-tagged sentences**, and $0 in steady state. Two findings worth recording: the sense-fit call
is *half* the cost of a many-sense entry ($0.001025 of `bank`'s $0.002136), because nano runs at
`low` reasoning under the shared `HYGIENE` policy and reasoning tokens are billed as output —
moving that call to a stage of its own at `reasoning_effort="none"` (D-38's fix) is the obvious
next saving, and is not taken here because `HYGIENE`'s policy is shared with four other passes.
And the one sentence the check dropped ("The merchant banks settlement funds through an automated
electronic transfer system.", written for *to deposit funds*, judged to be the institution sense)
is arguably a false positive: the check is conservative, which is the direction that costs a
sentence rather than files a wrong one.

**Consequence.** `StageName.EXAMPLES` is added with its own policy (luna, `low`, `max_tokens=8192`,
`expected_output_tokens=1200` — measured, and set above the mean because this is the one stage
whose output scales with the entry rather than sitting around one); `DraftExampleBatch` /
`DraftSenseExample` go in `contracts.py` and `EXAMPLES_INSTRUCTIONS` / `build_examples_prompt` in
`prompts.py`, where a stage's cached prefix belongs, while the sense-fit contract and instructions
stay module-private for D-49's and D-50's reason. `PROMPT_VERSION` moves to `7`. CLI: `opengloss
examples --from-list/--all [--limit --offset --per-sense --budget --concurrency --dry-run]`, whose
dry run reports the exact count of entries lacking the marker and an estimate from the measured
per-call means above. Nothing here touches `data/core-store`. Tests: **+18**
(`tests/test_examples.py`), with `tests/conftest.py` gaining a fifth append-only block whose
generation payload is a function of the *listed senses and targets* so one scripted answer can
carry an acceptable sentence, an exact repeat of it, one over the word cap, one that never names
the headword and one shaped like a definition at once.

## D-62 (2026-09-03) — Retrieval-data schema: queries, QA pairs and contrasts, keyed so a duplicate cannot be stored

**Context.** `docs/RETRIEVAL-DATA-PLAN.md` adds nine features aimed at
`../opengloss-embedding`, and four of them (F2 `queries`, F5 `contrasts`, F6 `qa_pairs`,
F9 `export-pretrain`) need somewhere to put what they write. Those four are built
concurrently, in separate worktrees, by separate agents; if each added its own fields the
shared file would be a merge conflict and the three id schemes would disagree. So the
schema lands first, on its own branch, with nothing but the storage and its rules — no
stage, no prompt, no CLI command. The one hard constraint is that a production chain is
running against `data/core-store` right now: whatever lands must leave every entry
already on disk valid, byte for byte, without a migration.

**Decision.** Three models — `Query` and `QAPair` on `Sense`, `Contrast` on `Lexeme` —
plus the four closed vocabularies they read from (`QueryStyle`, `QuestionType`,
`Difficulty`, `ContrastVerdict`), exactly as the plan sketches them. `docs/SCHEMA-V3.md`
§ 9 is the field-level reference; the four choices worth recording are these.

1. **Everything defaults empty, so there is no migration.** `Sense.queries`, `Sense.qa`
   and `Lexeme.contrasts` are `default_factory=list`, and nothing else about an entry
   changes. The proof is a test, not an argument: `tests/test_retrieval_schema.py` loads
   all 300 real entries in `data/sample-300` (copied out of the production store) and
   validates every one — **300/300 pass, none carries any of the new fields** — and a
   second test dumps 25 of them with `exclude_defaults=True` and asserts the result has no
   key the file on disk did not already have. `migrate.py` is untouched.

2. **Ids are positional and zero-based; the collection's *identity* is what it is keyed
   on.** `identity.query_id`/`qa_id` derive `<sense_id>#q3` / `<sense_id>#qa3` — zero-based
   like `sense_id`'s own index, not one-based like the provenance table's `p1`, `p2`,
   which are dictionary keys handed out on insertion rather than list positions. The
   consequence is stated in both docstrings: reordering renames, so append, never insert.
   Contrasts get no positional id at all — `edge_id` is the identity, which is what makes
   "one contrast per edge" expressible as a uniqueness rule instead of a convention.

3. **Uniqueness is validated where the row would be stored.** A duplicate query is not a
   second way of asking; it is one training pair twice, and a positional id would give the
   two copies different names, so nothing downstream would see them as duplicates. Queries
   and questions are keyed on `normalise_query_text` (case-fold, collapse whitespace
   runs, strip terminal `.,;:!?…` — and nothing else, because word order and spelling
   distinguish two things a user actually typed); contrasts on `edge_id` verbatim. A QA
   pair is keyed on its question alone: two answers to one question are a disagreement to
   resolve, not two rows.

4. **Two things are deliberately *not* validated.** `Contrast.edge_id` is not checked
   against the entry's live `edges()` — retiring a sense would then invalidate a stored,
   still-true paragraph, and a contrast for a since-removed relation is evidence about
   that removal, not a corrupt record. `QAPair.grounded_in` is not checked against
   `Lexeme.rendition_ids()`, and defaults empty: grounding is F6's post-check on the
   answer text, and a schema-level check would refuse to *load* an entry whose renditions
   were re-levelled after its QA pairs were written.

**Consequence.** `schema.py` gains the four enums, the three models, the public
`normalise_query_text`, the fields, three validators and three helpers
(`Sense.query_ids`, `Sense.qa_ids`, `Lexeme.contrast_for`); `identity.py` gains
`query_id` and `qa_id`, because every id in this project is derived there and none is
formatted at a call site. `StageName` gains `QUERIES`, `CONTRASTS` and `QA_PAIRS` — the
last deliberately not `QA`, which is the judge scoring stored content, not a stage writing
question/answer pairs — and `QAFlag` gains `OG_NEAR_COPY` (F7) and `OG_FILLER` (F8), so
those two agents need no further edit to a shared file.

Two consequential edits outside `schema.py` follow from adding enum members and are
recorded here rather than left as a surprise. `AppConfig._every_stage_has_a_policy`
requires a policy per `StageName`, so `config.py` registers all three now: nano for
`queries`, luna for `contrasts` and `qa_pairs`, per the plan's table, with
`expected_output_tokens` that are **estimates, not measurements** (500 / 400 / 900) —
D-41 wants the budget reservation set from measured output, so F2, F5 and F6 each replace
their own number from their pilot run and own the policy from then on. And
`tests/test_qa.py::test_every_flag_value_is_documented_in_the_instructions` requires every
`QAFlag` value to appear in `QA_INSTRUCTIONS`, so the two new flags join the list of
project-specific flags the judge is told to recognise but never use. `PROMPT_VERSION` is
**not** bumped for that: the added text names two flags in a "do not use these" list, it
cannot change a verdict, and a bump would invalidate every stored provenance's prompt
version and re-bill idempotence markers on a chain that is running right now — a real cost
for a no-op change.

No model was called and no money was spent: this branch adds storage, not a stage.
Nothing here touches `data/core-store` or `runs/`; `data/sample-300` was read only.
Tests: **+31** (`tests/test_retrieval_schema.py`).

## D-54 (2026-09-03) — `export-pairs`: WiC pairs and doc2query-shaped positives, free

**Context.** `docs/RETRIEVAL-DATA-PLAN.md` (F1) names the target consumer as
`../opengloss-embedding`, and observes that OpenGloss's sense-tagged, graph-linked entries make the
expensive part of embedding/reranker training data — hard negatives, paired positives — free:
every live sense already carries example renditions written *against that sense specifically*
(D-53), a gloss, and often an encyclopedia article, which is exactly the supervision a WiC
(word-in-context) task or an MS MARCO-style positive pair is normally paid an annotator or another
model to produce.

**Decision.** `src/opengloss_generator/export/pairs.py` (new `export/` package;
`export/__init__.py` is a bare docstring, kept minimal so sibling exporters — F3/F4's triples and
qrels, F9's pretrain serialiser — add modules rather than edit this one) reads a store and writes
one JSONL record per pair, five `PairKind`s:

* `wic_positive` (label 1) — every pairing of one live sense's own example renditions (not just
  the canonical one; a sense with `k` examples gives `C(k, 2)` pairs).
* `wic_hard_negative` (label 0) — one representative example from each pair of an entry's own live
  senses: same surface form, different meaning.
* `wic_easy_negative` (label 0, opt-in via `--easy-negatives N`, default 0) — one representative
  example from each of two live senses sharing a domain leaf but belonging to different headwords.
  The only sampled part of the export: each source sense seeds its own `random.Random(f"{seed}:
  {domain}:{sense_id}")`, so a draw never depends on iteration order, and the whole file is
  reproducible byte-for-byte for a fixed `--seed` (verified: two runs over `data/sample-300`
  diffed identical).
* `example_gloss` / `example_encyclopedia` (label 1) — a sense's representative example paired
  with its own canonical gloss, or with its entry's canonical encyclopedia rendition when one
  exists.

"Representative example" is a sense's canonical (neutral, plain) example if it has one, else its
first example in stored order. Only live senses of a non-retired entry ever appear in a pair —
retired senses, and entries whose own `status` is `retired`, contribute nothing on either side,
per the plan's non-negotiable. Output is sorted by `lexeme_id`, never by filesystem/store
iteration order, so the file is deterministic independent of `--seed` except for the easy-negative
rows. The record schema is the plan's ten named fields (`headword, sense_a, sense_b, text_a,
text_b, span_a, span_b, label, level_a, level_b`) plus two additive fields the plan's prose
requires but its field list didn't have room for: `headword_b` (differs from `headword` only for
`wic_easy_negative`) and `kind` (the `PairKind`, i.e. the "negative kind" the CLI summary reports
by). Full schema and one real record are in `docs/RETRIEVAL-DATA.md`.

CLI: `opengloss export-pairs --store S --out pairs.jsonl [--from-list L] [--easy-negatives N]
[--seed N]`, modeled on the free, no-`RunSession` shape of `audit`/`stats` rather than the
budget/ledger machinery every model-calling command carries, since this makes no model call at
all. Prints a JSON summary of `entries_scanned`, `entries_with_pairs`, `pairs_written`, and counts
`by_label` and `by_kind`.

**Measured, `data/sample-300` (300 entries, real run, no model call, `data/core-store` never
touched):**

Without `--easy-negatives` (default 0):

```json
{
  "entries_scanned": 300, "entries_with_pairs": 300, "pairs_written": 22684,
  "by_label": {"0": 1836, "1": 20848},
  "by_kind": {
    "example_encyclopedia": 1040, "example_gloss": 1040,
    "wic_hard_negative": 1836, "wic_positive": 18768
  }
}
```

With `--easy-negatives 3 --seed 0`:

```json
{
  "entries_scanned": 300, "entries_with_pairs": 300, "pairs_written": 25586,
  "by_label": {"0": 4738, "1": 20848},
  "by_kind": {
    "example_encyclopedia": 1040, "example_gloss": 1040, "wic_easy_negative": 2902,
    "wic_hard_negative": 1836, "wic_positive": 18768
  }
}
```

All 300 sample entries (285 of them multi-sense) contributed at least one pair; the mean is ~75-85
pairs per entry, dominated by `wic_positive` because sample-300 entries typically carry 5-9 example
renditions per sense across reading levels.

**Consequence.** Tests: **+21** (`tests/test_export_pairs.py`), fully offline like `test_audit.py`
— no `tests/conftest.py` change, since there is no model call and therefore no payload to script.
One additive import and one command registration in `cli.py`; no other shared file touched. Open
question left for the embedding project, not this feature: whether `wic_hard_negative` should be
capped per entry (an entry with many live senses gives `C(k, 2)` hard negatives, which can
outweigh its `wic_positive` count for a highly polysemous headword) — not capped here, since the
plan asks for "all pairs" and a downstream consumer can always subsample a JSONL file for free.
## D-60 (2026-09-03) — `qc filler`: a corpus-level, model-free filler detector, and 92% of encyclopedia renditions are boilerplate

**Context.** `workflows/content_hygiene.py`'s `stilted_examples` step (D-49) catches *known* academic
tells — `STILTED_RE` matches "researchers", "participants", "the study" — one canonical example at a
time. It cannot catch a tell nobody has named yet, and it never looks at the encyclopedia field at
all. `docs/RETRIEVAL-DATA-PLAN.md` (F8) asks for the complementary, model-free check: count 4-grams
and sentence openers across the *whole store*, and anything that recurs far more than chance is a
model habit, whatever words it happens to use — plus a per-entry uniqueness (type/token ratio) and
information-density score "in the spirit of" `alea-quality-model`'s `estimate_document_quality`
heuristic gate (band the words-per-sentence average: 0.8 inside 10-30 words, 0.6 inside 5-40, 0.4
outside both).

**Decision.** `src/opengloss_generator/qc/filler.py` (`qc/__init__.py` re-exports it), two literal
passes over `store.iter_entries()`, exactly as the plan specifies:

1. **Count.** Every non-retired sense's example renditions and every entry's encyclopedia
   renditions are split into sentences; each sentence contributes its *set* of 4-grams (deduplicated
   within the sentence, so one repetitive sentence cannot inflate its own count) and its 2- and
   3-word openers to a corpus-wide tally. A key's frequency is `(sentences containing it) / (total
   sentences)` — document frequency, which is what "appearing in >X% of sentences" means, not a raw
   occurrence count.
2. **Score.** A key clears the bar at `frequency > threshold` (4-grams 0.05%, openers 0.5% — the
   plan's own numbers, both configurable) **and** `count >= min_count` (default 5, a floor so a small
   store's tiny denominator cannot turn one repeated pair into a "finding" — not in the plan text,
   added because a literal reading of the frequency-only rule flags noise on any corpus under a few
   thousand sentences). A rewalk over the corpus (pass 2) locates every rendition with at least one
   over-threshold sentence — the "offending" renditions — and lists up to 3 example rendition ids per
   finding. `--flag` sets the new `QAFlag.OG_FILLER` on each offender's `Assessment` (creating one if
   absent); `--unflag` recomputes the same offending set and removes it. Both go through
   `apply_filler_flags`, one entry per work item with its lock held across read and write (D-31), and
   both re-locate the rendition by `(level, register, text)` rather than trusting a position computed
   before the lock was taken — `workflows/graph_hygiene.py`'s plan-then-apply discipline. Both are
   idempotent: `Assessment.flag` already dedupes, and a write only happens when something actually
   changed, so a second `--flag` or a `--unflag` on a clean store touches disk zero times.

An example rendition has no derivable id (`Lexeme.rendition_ids` deliberately excludes examples —
several may share one `(level, register)` key), so the report mints one: sense id, `(level,
register)` key, and position in that sense's example list, e.g. `abseil:verb:0#neutral/plain#0`.
Only the report reads it; flagging re-locates by content, never by this position.

`QAFlag.OG_FILLER = "og_filler"` is added to `schema.py` without the `og.` prefix every other
project-specific flag uses, on purpose: another branch is adding the same member to
`retrieval/schema` independently, and matching its exact spelling makes that merge a no-op instead
of a conflict. Two minimal, necessary edits followed from adding it: `QA_INSTRUCTIONS`'s closed QA
vocabulary list now names it beside the other five `og.*` flags (a flag the judge cannot see is a
flag it cannot recognise as already-handled, and `tests/test_qa.py` enforces this for every
`QAFlag` member), and `PROMPT_VERSION` moves `7` -> `8` per the module's own rule ("bump it whenever
instruction text changes"). CLI: `opengloss qc filler --store S --out report.json [--flag|--unflag]
[--from-list L] [--ngram-threshold --opener-threshold --min-count]`, under a new `qc` command group
(the CLI had none; F8 is the first free, measure-only-by-default pass with its own module, so it
gets its own group rather than a bare top-level verb).

**Measured on `data/sample-300`** (a copy in the worktree; nothing written, no `--flag`):

| | value |
|---|---|
| entries scanned | 300 |
| live senses | 1,041 |
| units scanned (examples + encyclopedia renditions) | 8,155 (6,655 example, 1,500 encyclopedia) |
| sentences scanned | 36,251 |
| over-threshold 4-grams | 75 |
| over-threshold 2-word openers | 10 |
| over-threshold 3-word openers | 2 |
| offending renditions (`--flag` candidates) | 1,467 |
| — of which encyclopedia | 1,385 (**92.3%** of all 1,500 encyclopedia renditions) |
| — of which example | 82 (1.2% of 6,655 example renditions) |
| entries with >=1 offending rendition | 300 / 300 |

Top findings, by sentence count: the 4-gram **"during the twentieth century"** (75 sentences, 0.21%
— `kennedy:encyclopedia#grade_5/plain`, `#grade_10/plain`, `#college/plain`), **"within broader
frameworks of"** (73), **"nineteenth and twentieth centuries"** (71), **"the word comes from"** (65);
the 2-word opener **"it can"** (478 sentences, 1.32% — `projection:encyclopedia#grade_1/plain`,
`#grade_5/plain`, `#grade_10/plain`), **"the word"** (406), **"related concepts"** (363, almost
entirely the 3-word opener "related concepts include", 294 sentences on its own). Every one of these
is encyclopedia connective tissue — cross-reference and etymology transitions ("the word comes
from", "the term derives from", "comes from the latin/an old"), hedges ("it can also mean"),
period-naming filler ("during the nineteenth and", "the late twentieth century") — never example
prose, matching the 92.3%-vs-1.2% split above: the four graded encyclopedia renditions per entry
share a template the model reaches for regardless of headword, in a way the sense-grounded example
sentences (D-53) mostly do not.

Entry scores: `information_density` is `0.8` for all 300 entries (every entry's average sentence
length across its own text falls inside the 10-30 word "ideal" band, 10.7-16.7 measured) —
uninformative at this corpus's actual sentence-length distribution, which is worth recording as a
finding in itself: the band is calibrated for arbitrary web-scale documents, not for a store whose
every sentence was already generated to a length constraint. `uniqueness` (type/token ratio over an
entry's *concatenated* text) ranges 0.234-0.430, mean 0.305 — the lowest scores (`euros` 0.234 over
2,092 words / 139 sentences, `henry` 0.245, `prefer` 0.247) belong to the entries with the most text,
which is TTR's well-known length bias rather than a defect specific to these entries; a per-entry
score should not be compared across entries of very different length without normalising for it,
which this module does not attempt (out of scope for a free heuristic gate; noted for whoever builds
on this number next).

**Tests:** +6, `tests/test_qc_filler.py` — an obvious six-entry stilted-encyclopedia-style corpus
is caught (4-gram and both opener lengths, exactly the six offending renditions and none of ten
varied control entries); the varied corpus alone produces zero findings; a retired sense's example
is excluded from both the count and the offender set; per-entry scores separate a three-word
fragment from a well-formed 24-word sentence; `--flag` is applied and a second detect-then-flag
cycle changes nothing (`renditions_flagged=0`, `renditions_already=6`); `--unflag` reverses it and
is itself idempotent against an already-clean store. `uv run ruff check/format`, `uv run ty check`,
and `uv run pytest` (792 passed) are clean.

**Left undone.** No rewrite pass reads `OG_FILLER` yet — the plan scopes that to "a later rewrite
pass", out of F8. The frequency and count thresholds are the plan's own numbers plus one addition
(`min_count`) not in the plan text; they have not been tuned against a judged sample the way D-51's
vocabulary bands were, so a `--flag` run's candidate set should be spot-checked before it drives a
rewrite pass, not trusted blind. `information_density`'s band is copied from `alea-quality-model`
unchanged and, per the measurement above, does not discriminate on this store; a future user of the
score should recalibrate the band or drop the dimension rather than read `0.8` as "fine."

## D-56 (2026-09-03) — `export-triples` / `export-qrels`: the graph pays for its own hard negatives

**Context.** F3+F4 in `docs/RETRIEVAL-DATA-PLAN.md`: the target consumer,
`../opengloss-embedding`, needs MS MARCO-style `(query, positive, negative)` triples and
TREC-style graded qrels. Mining hard negatives for either format normally costs a model
call or a human annotator; OpenGloss's sense-tagged, resolved semantic graph makes the
usual hard-negative kinds (a same-headword sense, a `confusable_with` target, a
co-hyponym, a synonym-of-a-synonym) free reads off data the store already holds. F2
(`Sense.queries`, doc2query-style synthetic queries) is a sibling feature on a different
branch and has not landed on `main`.

**Decision.** Two pure, offline modules, `export/triples.py` and `export/qrels.py`,
sharing one corpus projection (`triples.load_corpus`) and one candidate classifier
(`triples.classify`) so the two exports can never quietly disagree about what a given
sense's graph neighbourhood looks like. No model is called anywhere in either module.

1. **The query, absent F2.** Every query is read with `getattr(sense, "queries", [])`,
   so this code runs unchanged the day F2 merges. Today that list is always empty, so
   every sense's `grade_5/plain` gloss rendition (or its canonical `neutral/plain` gloss,
   when `grade_5` is missing) stands in as a single pseudo-query, and every record
   carries `query_source` — `"generated"` for an F2 query, `"gloss_pseudo"` for the
   fallback — so a trainer can tell the two apart, or filter pseudo-queries out entirely,
   once real queries exist for the same senses. A pseudo-query is close to the text it
   retrieves; that is the honest cost of not having F2 yet, which is exactly what the
   flag is for.

2. **Priority-ordered fallback, not an enumeration.** The plan lists the hard-negative
   kinds "in priority order": `other_sense` (another live sense of the same headword),
   `confusable` (a `confusable_with` target), `co_hyponym` (a sibling sharing a direct
   hypernym), `synonym_of_synonym` (distance-2 synonym). Read as a fallback chain — the
   first non-empty tier wins — this keeps `export-triples` a *triples* format: one hard
   negative per query, plus `--easy-negatives` (default 1) random-headword negatives, not
   up to four hard-negative rows per query. `export.triples.classify` partitions every
   related sense into exactly one of these tiers (plus `synonym`, never offered as a
   negative — see below), highest priority first, so a sense that qualifies for two tiers
   is kept only in the stronger one and is never offered under two `negative_kind` values.

3. **A direct synonym is never a negative.** `synonym_of_synonym` (distance 2) is a hard
   negative; a *direct* (distance-1) synonym is excluded from every negative tier,
   because it is close enough to the query's own meaning that training against it as a
   negative would teach a false contrast. `export-qrels` gives it partial credit instead
   (grade 2) — the two exports treat the same relation consistently, just for different
   purposes (a strict contrastive negative vs. a graded relevance judgement).

4. **Qrels grades and their pool caps.** 3 = the query's own sense; 2 = a direct synonym
   (capped at 3, `MAX_GRADE_2`, sampled deterministically above that); 1 = a direct
   hypernym or a co-hyponym (capped at 3, `MAX_GRADE_1`); 0 = everything else — every
   `export-triples` hard-negative kind **except** `co_hyponym` (already fully spent on
   grade 1; reusing it for grade 0 would let one sense land at two different grades for
   the same query — caught by `tests/test_export_qrels.py`'s
   `test_a_candidate_never_appears_at_two_grades_for_one_query` before this fix, see
   below) plus random easy negatives, padded up to `MAX_GRADE_0` (3). The easy-negative
   pad additionally excludes every sense grade 3/2/1 already claimed for that query — the
   graph tiers are disjoint by construction, but the easy pool (store-wide minus the
   query's own lexeme) knows nothing about them and can otherwise redraw a co-hyponym.

5. **Determinism.** Every random choice — which of a sense's gloss/example/encyclopedia
   text is the positive, which candidate wins a tied tier, which easy negative is drawn —
   comes from a fresh `random.Random` keyed `f"{seed}:{sense_id}:{...}"`
   (`triples._rng`), never from dict/set iteration order or one shared generator whose
   state depends on visitation order. The same `(store, seed)` always produces the same
   file. `--limit` caps entries scanned for a fast smoke run.

6. **A known limitation of testing this on a small sample.** `data/sample-300` is 300
   lexemes drawn from a much larger store; a resolved relation whose target lexeme was
   not itself sampled projects to nothing (`_resolve_graph` requires both ends live in
   the loaded corpus). That undercounts `confusable`/`co_hyponym`/`synonym_of_synonym` on
   a small sample relative to a full-store run — confirmed correct by construction in
   `tests/test_export_triples.py`'s hand-built world (which exercises all four hard-negative
   kinds and the full fallback chain directly, independent of sample size) — and is not a
   bug in the export, just a property of subsampling a graph.

**Measured on `data/sample-300`** (300 entries, `--seed 0`):

| | entries scanned | live senses / queries | rows written |
|---|---|---|---|
| `export-triples` (`--easy-negatives 1`) | 300 | 1,041 | 2,038 triples |
| `export-qrels` | 300 | 1,041 | 4,269 qrels rows, 1,041 docs, 1,041 listwise queries |

`export-triples` negative-kind histogram: `other_sense` 997, `easy` 1,041; `confusable`,
`co_hyponym`, and `synonym_of_synonym` did not fire on this sample (see point 6 — none of
the 300 sampled lexemes had a resolved `confusable_with` edge at all, and the sampled
hypernym/synonym-of-synonym edges whose *targets* also happened to be resolved to
in-sample senses were rare). `export-qrels` grade histogram: grade 3 → 1,041; grade 2 →
48; grade 1 → 57; grade 0 → 3,123. Every `query_source` in this run is `gloss_pseudo`,
since F2 has not landed. Both commands touch nothing but the store they are pointed at
(read-only; `data/core-store` and `runs/` are untouched) and spend $0.

**Consequence.** `export/__init__.py` (docstring only, so a parallel `export-pairs`
agent's own addition to the same package merges without conflict), `export/triples.py`,
`export/qrels.py`; CLI `export-triples --store S --out FILE [--seed --easy-negatives
--limit]` and `export-qrels --store S --out-dir DIR [--seed --limit]`, both wired in
`cli.py` beside the other reporting commands (`show`/`stats`/`audit`), reading the store
directly rather than through `RunSession` — there is no budget, model, or ledger for a
$0, no-model export to account for. Tests: **+38** (`tests/test_export_triples.py` — 24,
`tests/test_export_qrels.py` — 14), both against a hand-built store exercising every
hard-negative and grade tier, none against `tests/conftest.py` (no model payload is
needed). `docs/RETRIEVAL-DATA.md` created with this section, one real triple and one
real listwise record from the run above.

## D-59 (2026-09-03) — Register renditions: a lexical-diversity target, and a free near-copy check, generation-time and on disk

**Context.** `RENDITIONS_INSTRUCTIONS`' REGISTERS block already asks for informal,
technical, formal, slang, in-house and marketing rewrites of the same one-sentence
gloss and says "the rewrites must differ from each other in the ways their targets
demand" and "a rewrite that could be mistaken for the source with two words changed has
failed" — but, as with D-39's headword-initial rule and D-45's headword-in-example rule,
stating a constraint on wording is not the same as measuring whether a returned
rendition actually met it. Before doing anything else, `scripts/near_copy_rate.py` (new,
zero model calls) measured every non-`plain`-register gloss rendition already on disk
against its sense's canonical gloss, over `data/sample-300`: **4,164 register
renditions across 1,041 senses with a canonical gloss, near-copy rate (Jaccard ≥ 0.9)
0.34% (14/4,164)**, mean lexical diversity 0.72 (median 0.75, stdev 0.19), and only
**21.8%** of renditions landing inside a 0.30–0.60 target band — the histogram's mass
sits at 0.6–1.0, i.e. the existing model already over-shoots diversity far more often
than it copies. So the near-copy defect the plan named is real but rare (roughly 1 in
300 register renditions), and the bigger gap between the prompt's stated intent and its
output is on the *other* side, which this feature does not have a lever for: it adds a
number the prompt was missing and a free backstop for the tail that still copies,
without inventing a second check for "too different" that the plan never asked for.

**Decision.**

1. **The metric (`hygiene.py`, next to `is_headword_initial`).**
   `content_words(text) -> set[str]` lowercases and tokenises on `[A-Za-z]+`, dropping a
   small, module-private, closed stopword list (`_STOPWORDS` — articles, pronouns,
   conjunctions, common prepositions, the closed forms of "be"/"have"/"do"; deliberately
   conservative, since a stopword slipping through only makes two texts look *less*
   diverse than they are, the safe direction for a near-copy check to err in).
   `lexical_diversity(a, b) -> float` is `1 - Jaccard` over the two texts' content-word
   sets, `0.0` by convention when both sets are empty rather than raising on a zero
   division. `is_near_copy(a, b, *, threshold=NEAR_COPY_JACCARD_THRESHOLD)` is the
   yes/no verdict at the plan's own threshold, `NEAR_COPY_JACCARD_THRESHOLD = 0.9`.
   Not reused from `migrate.py`'s existing `FUNCTION_WORDS`: that list exists to decide
   whether a *headword* is a function word, an unrelated concern, and coupling a
   hygiene check every generation call runs through to a one-time schema-migration
   module would be the wrong dependency direction.
2. **The prompt (`prompts.py`).** `RENDITIONS_INSTRUCTIONS`' REGISTERS section gains one
   paragraph, after the worked register examples and before "WHAT THE FIELD MEANS FOR
   YOUR OUTPUT": a register rewrite of the gloss must land at 0.30–0.60 lexical
   diversity against the canonical gloss (defined the same way the check measures it —
   "1 minus the overlap of the two sentences' content words" — so the number in the
   prompt and the number in the check are the same number), a floor-and-ceiling framing
   ("below 0.30 you have copied the source ... above 0.60 you have likely drifted from
   its meaning") and an explicit ban on a verbatim or two-word-swapped copy, with a
   worked bad example. `QA_INSTRUCTIONS`' closed-flag list also grows the new flag name
   (every existing `QAFlag` member has to be listed there, and a standing test enforces
   it). `PROMPT_VERSION` moves `"7"` -> `"8"`.
3. **Generation time (`workflows/enrich.py`), exactly D-39/D-45/D-51's shared-retry
   shape applied to a fifth check.** A `gloss`-field rendition at any register but
   `plain` is measured with `is_near_copy` against `work.source` (the sense's canonical
   gloss, already markdown-stripped by `_sense_work`); a hit is a miss sharing the one
   retry with the other four checks, `_build_feedback` growing a fifth optional section
   from the new `prompts.build_near_copy_feedback(headword)`, and `_is_better`'s
   ordering gains a fourth tier — placed after headword-absent and before the
   unfamiliar-word share, since (unlike headword-initial/absent) there is no
   general-purpose tiebreak available when both candidates still copy, so it falls
   through to the next check exactly as headword-absent's own tie does. `plain` is never
   checked — it is the canonical's own register, not a rewrite meant to diverge from it
   — and, unlike D-39's headword-initial rule, **there is no proper-noun exemption**
   (D-59 mirrors D-45 here, not D-39): a proper noun's formal and slang registers still
   have to read differently from each other. `ReadabilityConfig.near_copy_retry: bool =
   True`, independent of `enabled`, gates the check exactly as its four siblings' own
   flags do. What is still a near-copy after the retry carries the new
   `QAFlag.OG_NEAR_COPY`, added with the literal value `"og_near_copy"` rather than the
   `"og."`-dot-prefixed convention every sibling flag uses, to match byte-for-byte a
   member of the same name landing concurrently on another branch, so the merge needs no
   reconciliation.
4. **What is already on disk (`workflows/retrofit.py`'s `rendition_hygiene` pass), flag
   only.** A free step rides alongside the pass's existing headword-initial rewrite:
   every stored non-`plain` gloss rendition is measured against its sense's canonical
   gloss with `is_near_copy` and `OG_NEAR_COPY` is set or cleared to match, on every
   sweep, with no attempt marker — there is no cost to bound. Unlike the headword-initial
   step this one spends nothing on a rewrite call: a paraphrase a model was already told
   to write differently is not fixed by asking the same model the same thing again, so
   the verdict is recorded for a later, dedicated rewrite pass to act on rather than
   acted on here. A near-copy flag flipping is, on its own, reason enough to write the
   entry even when the entry has no headword-initial offender at all, so `_clean_renditions`
   folds both signals into the one `items_changed`/`needs_write` decision the pass
   already made per entry.

**Consequence.** Zero measured cost: the generation-time check adds at most one shared
retry to calls that would already exist (never a new call on its own, since a rendition
request already crosses the register axis), and the retrofit step is a pure read/compare
over text already on disk. The `data/sample-300` baseline above stays the number to beat
until the store is regenerated under `PROMPT_VERSION = "8"` or swept by
`rendition_hygiene`; because the population is small (14 near-copies) and skewed toward
*more* diversity than the target band, the useful next measurement is not "did the
near-copy rate drop" so much as "did the >0.60 share move down toward the band" — outside
this feature's scope, since only the near-copy tail has a check, not the over-diverse
majority. Nothing here touches `data/core-store`. Tests: **+27**
(`tests/test_hygiene.py`, new, 13 pure-function cases for `content_words` /
`lexical_diversity` / `is_near_copy`; `tests/test_enrich.py`, +9, generation-time
rejection/retry/flag/off-switch/proper-noun/field-scoping/`_is_better` coverage plus a
new `NEAR_COPY_HEADWORD` marker in `tests/conftest.py`; `tests/test_retrofit.py`, +5, the
retrofit flag/clear/exempt/plain-register/proper-noun coverage sharing the existing
`_entry_with_gloss_renditions` helper) plus the QA-instructions flag-list fixture already
covered by the standing `test_every_flag_value_is_documented_in_the_instructions` test.

## D-61 (2026-09-03) — `export-pretrain`: four templates read straight off the entry, no model call

**Context.** F9 in `docs/RETRIEVAL-DATA-PLAN.md` wants pretraining tokens for
`../opengloss-embedding`, and an OpenGloss entry already holds four kinds of reference
prose a model call would otherwise have to invent: the dictionary entry itself (POS,
numbered senses, examples), a thesaurus entry (the sense's `synonym`/`antonym`/
`hypernym`/`see_also` relations), an encyclopedia article (the `encyclopedia` and
`lexical_explanation` renditions plus `etymology`), and a usage note (register variants
of the gloss, plus D-62's `Lexeme.contrasts` when F5 has filled them). This is free
because it is pure serialisation.

**Decision.** `export/pretrain.py` renders each template as plain prose/light markdown
(`#`/`##` headings, `-` example bullets, no JSON/YAML, no special tokens) and never
emits a section with nothing in it.

1. **Retired senses are invisible.** Every renderer that touches senses filters
   `sense.retired` before it looks at anything else, so a retired sense contributes no
   gloss, no example, no relation, and no register variant to any of the four
   templates — the same rule the plan states for every retrieval-data export.
2. **A section with nothing to say is left out, not emitted empty.** The thesaurus
   template skips a sense with none of the four relation types; the encyclopedia
   template's three `##` sections (`Overview`/`Etymology`/`Why This Word`) appear only
   when the underlying field has text; the usage note's per-sense register block and
   its `## Related Terms` block each appear only when at least one register variant or
   contrast actually rendered. A whole template is skipped for an entry with nothing at
   all for it to say (tested directly: a sparse entry with only a canonical gloss
   renders `dictionary` and nothing else).
3. **`--levels` selects reading levels; a miss falls back to neutral and says so.**
   Every leveled lookup tries `(level, plain)` (or, for a usage-note register line,
   `(level, register)`) first and falls back to the canonical `(neutral, plain)` (or
   `(neutral, register)`) text when that specific rendition is absent. The returned
   record's `level_used` is the requested level only when *every* section of that
   document matched exactly; if any section fell back, the whole document reports
   `level_used="neutral"` — a reader should not have to guess which paragraph of a
   mixed document is the one that got simplified.
4. **`--per-entry`/`--seed` mix templates across the corpus, not within one call.**
   Which of an entry's *available* templates it gets (when `--per-entry` asks for fewer
   than are available) is drawn once per entry from `random.Random(f"{seed}:
   {lexeme_id}")` — the same seeding convention `workflows/qa.py`'s stratified sample
   and `workflows/walk.py` already use — so the choice is deterministic per `(seed,
   entry)` and independent across entries, and a corpus built with a small
   `--per-entry` still mixes templates rather than always keeping the same subset.
   Availability itself is checked once, at `neutral`, since every renderer's canonical
   fallback guarantees that whatever content exists at all is visible there; this keeps
   the template draw one decision per entry rather than one per `(entry, level)` pair.
5. **Ids are derived, like every other id in this project (D-1):**
   `<lexeme_id>#pretrain-<template>-<level>`. Entries are visited in `lexeme_id` order,
   not on-disk shard order, so the JSONL is byte-identical across machines for the same
   inputs — verified directly (`test_export_pretrain_is_deterministic_across_runs`
   writes the same store twice and diffs the files).

**Measured on `data/sample-300`** (300 entries, all four templates,
`--levels neutral,grade_5,college`): 3,600 documents, 1,122,835 words total,
2,392 exact-level documents and 1,208 neutral-fallback documents.

| | dictionary | thesaurus | encyclopedia | usage_note |
|---|---:|---:|---:|---:|
| documents | 900 | 900 | 900 | 900 |
| words | 114,226 | 130,083 | 588,513 | 290,013 |

| | neutral | grade_5 | college |
|---|---:|---:|---:|
| documents | 1,200 | 1,200 | 1,200 |
| words | 393,378 | 349,142 | 380,315 |

Encyclopedia carries most of the corpus's words (the `encyclopedia`/`etymology`/
`lexical_explanation` fields are already full paragraphs; the other three templates
serialise short, structured facts). `data/sample-300` is a fixture shared across every
retrieval-data feature's worktree in this build round; F5 (`contrasts`) was writing to
it concurrently while this feature's own smoke run was taken, so the exact word counts
above are a snapshot, not a value anything downstream should assume is reproducible
bit-for-bit against a store that keeps changing underneath it -- only the *shape* of the
numbers (encyclopedia dominant, near-even across templates otherwise, roughly a third
of documents falling back to neutral because grade_5/college renditions are not
universal across `data/sample-300`) is the claim.

**Consequence.** `export/__init__.py` gets a two-line shared docstring only (F1/F3/F4
add their own submodules beside `pretrain.py` there without touching this file again);
`cli.py` gets one command, `export-pretrain`, registered the same free, no-`RunSession`
way `audit`/`stats`/`show` already are. No model was called and no money was spent.
Nothing here touches `data/core-store` or `runs/`; `data/sample-300` was read, and
written only by this feature's own `--out` path under `runs/`, never back into the
store. Tests: **+24** (`tests/test_export_pretrain.py`).

## D-57 (2026-09-03) — `contrasts`: one "X vs Y" paragraph per relation edge, written once per undirected pair, verdict recorded not acted on

**Context.** The store asserts 8.5M typed relation edges and explains none of them. A
`synonym` edge from *mail* to *post* says the two words mean the same thing, which is the
one claim a reader never needs help with; what a reader wants, and what an encoder cannot
learn from an edge list, is the sentence saying *when you would write one and not the
other*. That sentence exists nowhere in the resource — every other prose the pipeline
writes is about one sense in isolation. F5 of `docs/RETRIEVAL-DATA-PLAN.md` buys it, and it
is worth buying twice over: it is discriminative prose of a shape that is rare per token
anywhere, and it is the human-readable explanation of exactly the hard negatives F3's
`export-triples` mines from these same edges.

**Decision.** A `contrasts` stage on the luna policy. One call per **entry**, covering up to
`MAX_EDGES_PER_CALL` (8) of its eligible pairs, each shown as `[relation, A term, A gloss, A
example, B term, B gloss, B example]`; the answer is one 60-120-word paragraph per pair plus
a `ContrastVerdict`. Five choices are worth recording.

1. **One contrast per undirected pair, owned by the lexicographically smaller end.**
   `graph_hygiene` reciprocates symmetric relations, so a fully resolved synonym pair is
   visible from both ends; writing on both would double the bill for one fact and leave two
   paragraphs free to disagree. The end whose **sense id sorts smaller** owns the pair (sense
   ids begin with the lexeme id, so this is the plan's "lexicographically smaller lexeme id"
   with a deterministic tie-break within one entry) and the far end counts it as deferred.
   The test is conditional on the far side actually carrying the reciprocal: a
   one-directional edge is owned by whichever end asserts it, however the ids sort, because
   otherwise it would be deferred to an end that never looks at it and the pair would never
   be explained at all. On the pilot this halved the work exactly: 85 owned-or-deferred
   edges became **48 contrasts and 37 deferrals**.

2. **The far side is read lock-free, for prompt context only.** The far sense's gloss and one
   example come from a plain `store.read` of the target entry, memoised per sweep — the same
   thing `relation_hygiene._target_gloss` does. It is never a read this pass writes back
   from, so D-31's actual rule is untouched and no handler ever holds two entry locks. An
   edge whose target is unresolved, absent from this store, or retired is skipped and counted
   under one of two summary fields rather than guessed at from a bare surface form; on the
   pilot that was **1,152 unresolved and 6,982 not-in-store**, which is what a 300-entry
   slice of a 206K-entry graph looks like.

3. **The verdict is recorded, never acted on.** Writing the paragraph forces the model to
   look hard at whether the two senses really stand in the typed relation, so the verdict is
   free. D-50 gives relation edits to `relation_hygiene`, and a stage whose job is prose has
   no business deleting an edge on the strength of a by-product, so this pass stores the
   verdict, counts it in the summary, and changes nothing. It earns its place: on the pilot
   **19 of 48** came back `related_differently`, and reading them, most are the same real
   defect — a relation resolved to a sense of the wrong part of speech (`post` the verb
   pointed at `mail` the noun; `poorly` the adverb at `healthy` the adjective; `liverpool`
   the club at `team` the common noun). That is a work list for relation hygiene that cost
   nothing extra.

4. **Acceptance is deterministic, per paragraph, and there is no retry.** Empty, outside the
   45-160-word band (the asked-for 60-120 with slack), failing to name either term via
   `spans.find_span`, or quoting either gloss verbatim (normalised containment, glosses of 6+
   words). The naming and quoting checks are the cheap proxies for the failure this stage
   exists to avoid — two glosses restated and joined with "whereas". A rejected paragraph is
   counted by reason and dropped, as `examples` (D-53) argues for any stage that buys many
   interchangeable outputs per call. **The pilot rejected none of 48**, and every paragraph
   landed inside 90-111 words, so the band and the naming rules cost nothing and are there
   for the tail.

5. **D-47's marker, with the attempt counter reset rather than accumulated.** The sentinel is
   `contrasts:<digest>;attempts=<n>`, the digest taken over the sorted keys of the pairs
   **still outstanding**, each key being the edge id plus the digest of both glosses.
   `relation_hygiene` accumulates its counter because there a changed digest means "buy a
   second opinion" and accumulating is what bounds the spend. Here a changed digest means
   *progress* — the outstanding set shrank because paragraphs were stored, or grew because an
   edge resolved — so accumulating would turn the 2-attempt bound into a cap of 16 contrasts
   per entry ever, and an entry with 30 pairs would silently stop. Resetting leaves the bound
   doing D-47's actual job: an entry whose outstanding set is *unchanged* after a call, every
   paragraph refused, gets one more attempt and is then left alone. The consequence, stated
   rather than hidden: a contrast is written once per edge and is **not** refreshed when a
   gloss is later rewritten. One contrast per edge is the schema's own uniqueness rule (D-62)
   and a refresh pass is not in this plan.

**Measured (pilot, `data/sample-300`, `--budget 0.50 --concurrency 8`, run
`20260903T092949Z-959f9561`).** 300 entries scanned, 37 due, **37 calls, 48 contrasts
stored, $0.005954 total — $0.000124 per contrast, $0.000161 per call**, 25.7 seconds. Per
call: mean 1,821 input tokens of which 1,585 cached (**87.0% cache hit rate**) and mean
**202 output tokens** (median 159, max 430) — about 156 output tokens per paragraph, with
1-3 pairs per call on this store. Nothing was rejected; nothing stopped early. Verdicts:
`related_as_typed` 29, `related_differently` 19, `unrelated` 0. By type, exactly 24 synonym
and 24 antonym paragraphs and **no `confusable_with` at all**, because the 300-entry slice
contains none — the type is exercised by the offline tests only, and its first real
measurement will have to come from a core-store run.

Two consequences of the measurement. `config.py`'s `CONTRASTS` policy replaces D-62's
placeholder `expected_output_tokens=400` with **500**: the pilot mean is 202, but output
scales with pairs per call at ~156 tokens each against a cap of 8, and a 300-entry slice
under-represents pair density badly (most relation targets are simply absent from it), so
the reservation is set at roughly a three-pair call rather than at a number a full store
would blow past. And `cli.py` gets measured `--dry-run` constants (1,800 / 1,600 / 200),
which price the pilot store to within a fraction of a cent of what it actually cost.

**Quality, read rather than asserted.** All 48 paragraphs were read. They discriminate:
*mail*/*post* and *extremely*/*way* on region and register, *technique*/*way* on
specificity, *hum*/*roar* on intensity, *descending*/*descent* and *coming*/*upcoming* on
grammatical slot, *safe*/*secure* on general danger versus a specific threat. The antonym
paragraphs do what they were asked to: name the axis, say whether there is a middle, and say
which member is unmarked. None restates the two glosses. The one clear verdict miss is
`lower:verb:0-antonym->scales` — a bad edge the model gamely wrote a real paragraph about
and then called `related_as_typed`. The one stylistic finding worth writing down is that the
instructions' own vocabulary echoes back: **9 of 24** antonym paragraphs open on "sit/lie at
opposite ends", 13 use the word "axis", 8 use "unmarked". That is exactly the corpus-level
filler F8 exists to detect, and F8 should be pointed at this field once it lands rather than
the wording being tinkered with on a hunch.

**Consequence.** New `src/opengloss_generator/workflows/contrasts.py` (contract,
instructions and prompt builder module-private, following `examples.py`'s sense-fit call:
three sibling retrieval-data features are editing `contracts.py` and `prompts.py`
concurrently, and an append-only module cannot conflict with them). `cli.py` gains one
command and its dry-run estimate; `config.py` one measured number; `tests/conftest.py` one
payload and one registry line; `README.md` one row; `docs/RETRIEVAL-DATA.md` its first
section. **Not done, deliberately:** the sweep emits no per-item ledger records, matching
`examples`, `relation_hygiene`, `sense_hygiene` and `content_hygiene` — every pooled sweep
that owns its own worker pool leaves per-call accounting to the run log's `stage_complete`
records and the cost meter, and only the CLI-driven loops (`walk`, `enrich`, `resolve`,
`qa`, `retrofit`) emit to the ledger. Tests: **+26** (`tests/test_contrasts.py`), 843 pass.
`data/core-store` untouched.

**One operational note, recorded because it will bite the next agent.** `data/sample-300`
is a single shared directory and four retrieval-data pilots are being run against it
concurrently, while `tests/test_retrieval_schema.py::test_every_stored_sample_entry_still_validates`
(D-62) asserts that **no** entry in it carries `contrasts`, `queries` or `qa`. Those two
facts cannot both hold: any pilot that writes to the shared copy turns that test red for
every other agent, and reverting the store to satisfy it destroys a sibling pilot's output.
This pilot hit both halves of that — it wrote 48 contrasts, and the revert that followed
clobbered part of a concurrent `queries` pilot that had written into the same 300 files.
The fix used here is per-worktree isolation: point `data/sample-300` at a private copy of
the pristine store and pilot into that. The durable fix is either a per-agent sample store
or a `test_retrieval_schema` precondition that does not assume a pristine fixture; whoever
lands the next feature should pick one.

## D-55 (2026-09-03) — The `queries` stage: doc2query per sense, on luna rather than nano, because luna is both cheaper and better

**Context.** `docs/RETRIEVAL-DATA-PLAN.md` F2. Every text this project writes is an
*answer* — a definition, an example, an encyclopedia passage — and a retrieval encoder
needs the other half of the pair, the query a person actually typed. Doc2query is the
standard way to manufacture that half, and the store already has the two things that make
the manufactured queries worth more than a generic doc2query run over a text corpus: the
entry knows its own ambiguity (so a query can be *required* to discriminate one sense from
its siblings), and it knows the headword (so "did this query avoid naming it?" is a free
measurement rather than a hope). Both matter for the same reason: a query set that all
contains its own headword is solved by BM25 and teaches an encoder to match a string.

**Decision.** A new stage, `StageName.QUERIES`, in `workflows/queries.py`, `plan_queries` /
`run_queries`, CLI `opengloss queries --from-list/--all [--limit --offset --per-sense
--budget --concurrency --dry-run]`. The five choices worth recording:

1. **One call per *sense*, not per entry.** `examples.py` calls once per entry because an
   example sentence must fit its own sense and no other, so writing sense 2's sentences
   while looking at sense 1 is the whole trick. A query is a short, self-contained string;
   asking one answer for twelve of them across six senses is how a model comes back with
   four thrown-away queries per sense. The siblings' glosses are still in every prompt —
   they are what the queries must not fit — as context rather than as more work.

2. **Eight styles, and the count is in the prompt, not the instructions.**
   `QueryStyle`'s eight registers (keyword, question, conversational, constraint, role,
   example_based, step_by_step, directive) are the coverage plan, asked for at least once
   each; twelve queries against eight styles leaves four slack slots the model spends
   where the sense actually has more than one way in. The ~1.9K-token instruction block is
   byte-stable and carries the styles, the discrimination rule and the worked example, so
   it caches (measured 88% cache hit rate on the default model's pilot). `per_sense` is
   the one part of the ask that varies per run, so it lives in the volatile prompt.

3. **Everything checkable is checked for free, and containing the headword is *counted*,
   never refused.** A query is markdown-stripped and collapsed; empty, over 200 characters
   (the storage schema's own ceiling, enforced here so an over-long query is dropped rather
   than failing the entry's write), duplicated against the sense's existing queries or
   against one accepted earlier in the same answer, or returned past the count asked for,
   is dropped and counted by reason. Whether it names the headword is *measured*: refusing
   individual lexical queries would only teach the model to smuggle the headword in as a
   paraphrase, and a keyword query for a rare technical sense reasonably names the word.
   What matters is the share, and `headword_free_share`,
   `senses_below_headword_free_target`, `senses_with_full_style_coverage` and
   `stored_by_style` are in the run summary for exactly that reason.

4. **A D-47 marker per sense, keyed on the canonical gloss.**
   `queries:<sense_id>:<digest of gloss + per_sense>;attempts=<n>`, on the answering call's
   own provenance record. A rerun over an unchanged sense costs $0; a rewritten gloss or a
   different `--per-sense` earns exactly one more call, and D-47's bound of two stops
   there. Queries are **appended**, never inserted, because `identity.query_id` is
   positional (D-62). A budget stop mid-entry is caught, the senses already answered are
   written, and only then does the stop propagate: throwing away an answer that has already
   been billed is the one thing a budget guard must not cause.

5. **The stage ships on `gpt-5.6-luna`, not the `gpt-5.4-nano` the plan's table proposed.**
   The plan said to pilot both and default to the cheaper one that passes. Luna is the
   cheaper one *and* the better one, which was not the expected result and is why it is
   recorded in detail below.

**Measured — the two pilots.** Both over real entries of `data/sample-300` at 12 queries
per sense, `--budget 0.50`, concurrency 8, on two disjoint 60-headword lists. Every number
is from that run's `runs/<run_id>.ledger.jsonl`, one record per call; nothing is projected
to the full store.

| | nano (`20260903T093021Z-0dab2ef3`) | luna (`20260903T093238Z-8d98ab11`) |
|---|---|---|
| senses / calls | 213 | 218 |
| cost | **$0.086984** | **$0.053394** |
| cost per sense | **$0.000408** | **$0.000245** |
| output tokens per call (mean / median / max) | **544 / 476 / 1,212** | **330 / 319 / 602** |
| input tokens per call (mean, of which cached) | 2,250 (1,742) | 2,268 (1,999) |
| queries stored | 2,556 | 2,616 |
| rejected | 4 (all `surplus`) | 4 (all `surplus`) |
| headword-free share | **0.585** | **0.782** |
| senses missing the ≥ ½ headword-free bar | 57 / 213 (26.8%) | 5 / 218 (2.3%) |
| senses covering all eight styles | 213 / 213 | 216 / 218 |
| wall clock | 131 s | 494 s |

The two lists are different senses, so a second, strictly head-to-head pass ran both models
over the *same* 43 senses (12 entries copied to a private store, 516 queries each), which
is where the quality numbers are cleanest: headword-free **0.616 nano / 0.816 luna**; senses
missing the bar **8 / 0**; mean query length **81 / 66 characters**; queries whose text
begins with the model's own style label leaking into it ("`keyword: atmospheric conditions
abbreviation atm`") **42 of 516 (8.1%) nano / 0 luna**; queries of the explicitly banned
"what does X mean" shape **7 nano / 0 luna**.

**Verdict on reading 20 queries from each.** Both models cover all eight styles and both
discriminate senses better than expected — nano's `advert` verb-1 set ("*I'm writing a
report and I keep wanting a verb for 'to just mention something briefly'*") is genuinely
about the passing-reference sense and not the promotion sense. The differences are in
*naturalness* and *hygiene*. Luna writes what a person types: "I need cash for a taxi but
the branch is closed", "I don't mean mentioning it in passing—I mean actively getting the
product known". Nano writes longer, more explanatory queries that drift towards being their
own answer, produces the occasional ungrammatical one ("What's the best way to adverts your
new service"), files an example *sentence* under `example_based` instead of a request for
one ("experiments were conducted at one atmosphere to ensure comparability"), and leaks its
style label into the text on one sense in twelve. So luna passes and nano is marginal — and
luna costs 40% less, because the per-token prices are within 4% of each other (luna is
actually the cheaper of the two on output) and nano spends 65% more output tokens per call,
most of them invisible reasoning tokens that are billed as output. The one thing nano wins
is latency, by 3.8×, which is throughput rather than money and is what `--concurrency` is
for.

**Consequence.** `config.py`'s `QUERIES` policy becomes `gpt-5.6-luna`, `low` effort,
`max_tokens=4096`, `expected_output_tokens=400` — the measured mean of 330 rounded up, per
D-41, against a largest observed answer of 602; the placeholder 500 the schema branch
registered is gone. `cli.py` gains the command, a measured dry-run estimate (2,270 input /
2,000 cached / 330 output per call) and one ledger record per sense, so a later run can
re-derive `expected_output_tokens` from measurement rather than from this document. The
output contract and the instructions are **module-private** in `workflows/queries.py`
rather than in `contracts.py` / `prompts.py`, following `sense_hygiene` and
`relation_hygiene`: eight sibling retrieval-data features are being built concurrently
against those shared files. `PROMPT_VERSION` is **not** bumped — no text in `prompts.py`
changed, and bumping it would re-bill idempotence markers on a chain that is running now.
Tests: **+23** (`tests/test_queries.py`), with `tests/conftest.py` gaining an append-only
block whose payload is a function of the count the prompt asks for, so one scripted answer
can carry an acceptable query, an exact repeat of it, one over the character ceiling, one
that is whitespace only and two past the count. `data/core-store` was never touched.

**Two things left open.** First, `data/sample-300` is now shared by nine concurrent agent
worktrees and is being written by several of them at once; roughly three quarters of this
pilot's stored entries were clobbered by a sibling between the write and the read, which is
why the head-to-head quality pass ran against a private copy. The ledger numbers above are
unaffected — they come from the provider, not from disk — but
`tests/test_retrieval_schema.py`'s two "the sample store carries none of the new fields"
tests now fail for everyone, on `retrieval/schema` HEAD as well as here, and that premise
needs retiring by whoever owns that file. Second, nano was piloted at `low` reasoning
effort; `"none"` (D-38's lever) would cut most of its 544 output tokens and might make it
competitive on cost again, but it would not fix the style-label leakage or the naturalness
gap, which are the reasons it lost, so it was not worth a third paid run.

## D-58 (2026-09-03) — `qa-pairs`: seven grounded question/answer pairs per sense, one call each, checked against what they cite

**Context.** F6 of `docs/RETRIEVAL-DATA-PLAN.md`. The consumer (`../opengloss-embedding`)
wants question/answer text; the pipeline can generate it freely, and generating it freely
would be a mistake. A QA pair written out of the model's own knowledge is a fact this
project neither verified nor paid for, sitting in a store whose entire claim is that its
content has been through a judge and a set of hygiene passes. So this stage writes pairs
**out of stored text only**, and the stored text is what makes the result checkable.

**Decision.**

1. **One call per live sense**, not per entry — the reverse of D-53's choice for
   `examples`. `examples` needs the whole inventory in one prompt so a sentence can be
   written to fit *only* its own sense. Nothing here does: a question about sense 2 is
   answered out of sense 2's own material, and showing the model the neighbouring senses
   would invite precisely the failure the grounding check exists to catch. The unit of
   *locking* is still the entry (D-31): read, one call per live sense, apply, write, all
   inside one hold.

2. **Four kinds of source, each labelled with an id the answer must cite**: the canonical
   gloss, up to six de-duplicated example sentences, the entry's `(neutral, plain)`
   encyclopedia passage capped at 500 words, and the etymology summary. Retired senses are
   skipped (D-52).

3. **Two of the four id forms are stage-local**, and this is the one place this project
   formats an id outside `identity.py`. `Lexeme.rendition_ids()` already documents why an
   example rendition has no derived id — several may share one `(level, register)` key —
   and worse, a sense's `(neutral, plain)` *example* and its `(neutral, plain)` *gloss*
   produce the **same** string under `rendition_id`, which is exactly the ambiguity a
   citation cannot have. So examples are addressed `<sense_id>#ex<n>`, in the `#q<n>` /
   `#qa<n>` family, and the etymology — not a rendition set at all — is addressed
   `<lexeme_id>:etymology`. Both live in `workflows/qa_pairs.py`. An id form with one
   consumer belongs next to that consumer; F1/F3's exports are the second consumer that
   would justify promoting them, and the plan's working rules do not list `identity.py`
   among the shared files this agent may edit.

4. **Three free post-checks, no retry.** A pair is dropped and counted when its
   `grounded_in` is empty (`no_citation`) or names an unsupplied id (`unknown_citation`);
   when its answer shares fewer than two content words with the text it cited
   (`not_grounded`); or when its normalised question repeats one already accepted or
   already stored (`duplicate_question`). There is no retry loop, for D-53's reason: the
   next sense's call buys seven more pairs more cheaply than a retry buys back one, and the
   reason counters are the feedback loop instead.

5. **D-47's marker, per sense**, on the generating call's own zero-cost provenance record:
   `qa_pairs:<sense_id>:<digest>;attempts=<n>`, two attempts maximum. The digest covers the
   sorted source ids **and the canonical gloss text**, because a rewritten gloss keeps its
   rendition id and is exactly the case where the stored pairs have gone stale.

**Pilot (sample-300, 2026-09-03, `runs/20260903T093145Z-0866e86d.ledger.jsonl`).** 1,034
calls at concurrency 16, 17.8 minutes, `--budget 0.50`, completed rather than stopped.

| measured | |
|---|---|
| cost | **$0.426915** (with the 7-call smoke run: $0.430123 for all 1,041 senses) |
| cost per sense | **$0.000413** |
| cost per accepted pair | **$0.000060** |
| output tokens per call | mean **510**, median 496, p90 585, max 959 |
| input tokens per call | mean 2,874, 2,006 of them cached (70% hit rate) |
| pairs generated / accepted | 7,238 / 7,121 (**98.4%**) |
| drops | `not_grounded` **100**, `unknown_citation` **17**; `no_citation` and `duplicate_question` never fired |
| drops per call | 933 calls lost nothing, 95 lost one, 4 lost two, 2 lost all seven |
| full 7-type coverage | 896 of 1,034 senses (86.7%) |
| difficulty mix | easy 3,084 / medium 2,974 / hard 1,063 |
| retries / failed calls | 0 / 0 |

The measured mean, 510 output tokens, **replaces the schema branch's provisional
`expected_output_tokens=900`** in `config.py` (D-41: the budget guard reserves at a
measured typical output, and 900 held nearly twice what a call spends).

**Quality verdict.** Three senses read in full — `projection:noun:1`, `firm:verb:0`,
`mediterranean:noun:2`, twenty-one pairs. Every citation resolved to text that genuinely
supports its answer; no invented fact was found in any of the twenty-one. The type labels
are doing real work at the top of the range: `projection`'s `reasoning` pair combines the
entry-level encyclopedia's *geometric* projection with this sense's *optical* one into a
statement neither source makes alone, and cites both, which is exactly the output this
stage was bought for. Verdict: ship.

Three defects are recorded rather than fixed, because each is a prompt change whose value
should be measured against a second pilot rather than assumed:

* **Meta-reference leakage, 7.9%** (498 of 6,289 stored pairs contain "the example(s)",
  "according to", "the passage", "the text", "the definition"). The instructions ban it
  explicitly and it happens anyway. A reader of these questions has never seen the sources,
  so "What kind of traditions do Mediterraneans have, according to the examples?" is a
  broken question. This is a **free post-check waiting to be written** — a regex over
  question and answer — and it is the highest-value next change.
* **Definition pairs restate the gloss verbatim, 11.6%** (105 of 907). The instructions ask
  for the meaning "in your own words"; a verbatim echo is a training row that teaches
  nothing the gloss did not.
* **`procedural` degrades into `factual`** when a sense describes no procedure ("How did
  projection change the empty warehouse wall?" is not a procedural question). Seven types
  per sense is a coverage plan, not a guarantee that all seven are natural for every sense.

The `not_grounded` floor is doing something, but less than it looks: at two content words
it is lenient by design, and one of the two is very often the headword itself, which
appears in nearly every answer and nearly every source. The two calls that lost all seven
pairs are the interesting case, and the drop rate should be read as *the floor is set where
a paraphrase is never punished*, not as *1.4% of answers were fabrications*. A stricter
variant — excluding the headword and its forms from both sides — is the obvious experiment,
and should be run against a pilot before it is adopted, because the failure mode it invites
(refusing honest short answers) is worse than the one it catches.

**Consequence.** New: `src/opengloss_generator/workflows/qa_pairs.py` (module-private
contract and instructions, following `sense_hygiene` and `relation_hygiene`, so this stage
does not touch `prompts.py` or `contracts.py` while three sibling agents are editing them);
`tests/test_qa_pairs.py` (+14); `docs/RETRIEVAL-DATA.md` § F6. Minimal additive edits to
`cli.py` (the `qa-pairs` command — **not** `qa`, which is the Opus judge, plus the dry-run
token constants), `config.py` (the measured `expected_output_tokens`), `tests/conftest.py`
(one payload builder and one registry line, appended), `README.md` (one table row).
`run_qa_pairs` takes an optional `on_call` sink so the CLI can put one ledger record per
call on the run ledger; without it, per-call output tokens and cost per sense could not be
recovered from a sweep's totals at all.

**Left undone, and one caveat about the pilot's store.** `data/sample-300` was written by
several sibling retrieval-data agents concurrently with this pilot. The store's lock is
per-entry and *in-process*, so cross-process writes are last-writer-wins: 6,289 of the
7,121 accepted pairs survive on disk, the rest having been clobbered by another agent's
write of the same entry. The ledger numbers above are unaffected — they record what this
run generated, accepted and paid for — but a later reader counting `Sense.qa` rows in
`data/sample-300` will find fewer than 7,121, and re-running `qa-pairs` over the sample
will re-bill the clobbered senses because their markers went with them. `data/core-store`
and `runs/` in the main checkout were untouched.

A second, related consequence: D-62's
`test_every_stored_sample_entry_still_validates` and
`test_stored_entries_serialise_back_to_what_they_were` assert that `data/sample-300`
carries *no* queries, QA pairs or contrasts. Both are guarded by
`skipif(not SAMPLE_STORE.is_dir())`, so they skip unless a worktree has linked the sample
store — and they now fail wherever it is linked, because the plan told F2, F5 and F6 to
pilot against exactly that store. They fail identically with this branch's changes
stashed, so they are not this stage's regression, and they are not this stage's file to
edit either; the assertion they need is "loads and round-trips", not "is empty". Recorded
here so the next agent to see red knows what it is. Without the link, `uv run pytest` is
829 passed, 2 skipped.

## D-63 (2026-09-03) — Writer rotation: multi-provider routing, and a five-writer pilot that clears two of five for production

**Context.** Every prose rendition in `data/core-store` has been written by one model,
`gpt-5.6-luna`. `docs/RETRIEVAL-DATA-PLAN.md`'s consumer (`../opengloss-embedding`) wants
diverse, high-quality tokens anchored to the ontology; a single writer's fixed style is
the opposite of diverse, whatever its quality. `shelf-benchmark`'s own generator-balanced
factorial design (its `docs/data_plan_v0.4.md` § 1) measured 93.1% attribution accuracy
across four balanced OpenAI generators — a single writer's fingerprint is large and
detectable — which is the direct motivation for testing whether this pipeline's writer
could rotate too, and at what cost.

**Decision.**

1. **The router (`router.py`) gained four provider shapes beyond OpenAI**: Anthropic
   (already supported), Google Gemini, OpenRouter, and a local OpenAI-compatible
   endpoint by base URL (for a future vLLM writer). `_split_model` replaces the old
   `_provider_prefix`: a model may carry an explicit routing prefix
   (`openai:`/`anthropic:`/`google:`/`openrouter:`/`local:`) or be routed by its own
   id's shape (`claude-` -> Anthropic, `gemini-` -> Google, a literal `/` -> OpenRouter's
   `org/model` catalogue convention, everything else -> OpenAI) — the same convention
   this project already used for Anthropic, extended rather than replaced. `model_for`
   and `settings_for` both take an optional `model` override so one call can use a
   different model than the rest of its stage's policy without touching the policy
   itself. Flex-tier and prompt-cache-key settings are built only in the OpenAI branch,
   so they can never reach a provider that would reject them. Google's lazy import
   (`_import_google_model_settings`) and `_infer_model`'s warning suppression exist
   because `pydantic_ai.models.google` imports `google-genai`, which raises a
   `DeprecationWarning` on this project's Python 3.14 interpreter — an upstream problem
   this project's own `filterwarnings = ["error"]` would otherwise turn into a test
   failure for every Google-touching test.
2. **`ModelPolicy` gained `writers: list[WriterOption] | None` and `writer_seed: int`.**
   `WriterOption` is `{model, weight}`; `ModelPolicy.writer_for(key)` draws one
   deterministically via `random.Random(f"{writer_seed}:{key}")` — the same key always
   draws the same writer, so a rerun of unchanged input is idempotent and the mix is
   auditable from provenance without replaying the draw. The price gate
   (`_model_must_be_priced`) now validates every writer's model, not only the policy's
   own `model`, so a misconfigured writer list is refused at construction, before any
   spend. `StageRunner.run()` takes an optional `writer_key`; the rendition and examples
   call sites pass a sense id (or, for the one-call-per-entry D-53 workflow, the same
   digest of the entry's live sense-id set its own completion marker already uses) —
   nowhere else needed to change.
3. **`Provenance` gained `provider: str | None`**, populated from
   `ModelResponse.provider_details["downstream_provider"]` when the router used
   OpenRouter, which reports which upstream actually served a call (observed in the
   pilot: `Phala`, `StreamLake`, `Venice`, `Parasail`, all serving the same nominal
   `qwen/qwen3.5-397b-a17b`). `None` for every other provider and for content written
   before this field existed.
4. **Price rows added for the pilot's non-OpenAI, non-Anthropic writers**
   (`qwen/qwen3.5-397b-a17b`, `deepseek/deepseek-v4-pro`, `gemini-3.7-flash`), verified
   2026-09-03 against the OpenRouter catalogue (`GET
   https://openrouter.ai/api/v1/models`, unauthenticated, the same way SHELF's
   `pricing.py` does) and, for `gemini-3.7-flash` (also reachable direct), cross-checked
   the same day against `https://ai.google.dev/gemini-api/docs/pricing` — an exact
   match, so the OpenRouter rate is also this table's direct-API rate.
   `claude-haiku-4-5`'s existing row already matched the catalogue; no change needed.
   The "no price row, no run" gate (D-6's ancestor pattern, `pricing.price_for`)
   continues to cover every writer, old and new.

**Pilot (`data/sample-writers/`, 300 entries, seed 11, never `data/core-store`).**
Full design, every measured number, provider failure modes with exact error strings,
the attribution/lexical-diversity/anchoring/gate-breakdown tables, three real sentences
per writer for one shared sense, and the recommendation are in
`docs/WRITER-DIVERSITY.md`. Summary here:

* **Two of five candidate writers are not viable with this pipeline as shipped.**
  `deepseek/deepseek-v4-pro` (OpenRouter): 100% failure, both tasks —
  `pydantic_ai.exceptions.UserError: Native structured output is not supported by this
  model`, raised client-side before any request is sent. `gemini-3.7-flash` (direct
  Google): graded-rendition task succeeded (176 calls, $0.76), but the D-53 multi-sense
  example-batch task failed 100% — `400 INVALID_ARGUMENT: Request contains an invalid
  argument`, with no further detail from Google's error body.
* **`claude-haiku-4-5` cleared both tasks cleanly**: $0.00373/rendition, $0.000585 per
  accepted D-53 sentence, no judge-score regression from luna (62.83 vs. 64.21 on
  Opus's 0-100 scale, both well inside this sample's noise band), and its
  attribution-model style tell is ordinary function words rather than a detectable
  fingerprint.
* **`qwen/qwen3.5-397b-a17b` (OpenRouter) works but is cost/latency-unpredictable**:
  normal calls run 200-350 output tokens; a recurring fraction blow up to
  5,500-8,186 output tokens (a reasoning MoE not fully respecting
  `reasoning_effort="low"`, billed as output like any reasoning model's hidden tokens),
  and one call failed outright on `max_tokens` before recovering on retry at 6,495
  output tokens. Its generated text also leaks literal prompt labels
  (`grade_10`, `college`, the sense's own headword) into sentence text — a real,
  visible style defect distinct from the cost problem.
* **Attribution (TF-IDF + logistic regression, balanced, 5-fold, SHELF's method)**:
  66.0% accuracy across the four writers that produced attributable text, against 25%
  chance — style is detectable well above chance, which is the point, but the number is
  confounded by uneven per-arm entry coverage (each arm's budget stopped at a different
  point in the alphabetically-ordered sample) and should be re-measured on a
  topic-matched subset before being trusted as a target metric.
* **Recommendation**: ship an 80/20 `gpt-5.6-luna`/`claude-haiku-4-5` rotation on the
  `RENDITIONS` and `EXAMPLES` stages via the new `writers` mechanism; treat `gemini-3.7-
  flash` as a task-(a)-only candidate pending its D-53 schema failure being understood;
  do not rotate in `qwen` or `deepseek` yet.

**Consequence.** New: `scripts/build_sample_writers.py`,
`scripts/reset_writer_arm.py`, `scripts/run_writer_pilot.py`,
`scripts/writer_diversity_report.py` (one-off pilot tooling; the last needs
`scikit-learn`, run via `uv run --with scikit-learn` rather than added as a package
dependency); `tests/test_writers.py` (+18, offline); `docs/WRITER-DIVERSITY.md`.
Minimal additive edits to `router.py` (multi-provider dispatch), `config.py`
(`WriterOption`, `ModelPolicy.writers`/`writer_seed`/`writer_for`), `schema.py`
(`Provenance.provider`), `stages.py` (`writer_key` threading and provider capture),
`pricing.py` (four new/verified rows), `workflows/enrich.py` and
`workflows/examples.py` (one `writer_key=` argument each at their existing
`runner.run()` call sites).

**Left undone, and one caveat about the pilot run.** Five to seven of the forty
sampled entries per judged arm failed to judge on `HTTP/1.1 529 Overloaded` from the
Anthropic API during this run; `stages.py`'s `_RETRYABLE_STATUS` does not include 529,
so the stage runner did not retry past the SDK client's own few automatic retries. This
is a real, small gap between this pipeline's actual retry coverage and a reasonable
assumption that the judge already retries provider overload — worth a one-line fix
(add 529 to `_RETRYABLE_STATUS`) but not made here, since a judge-reliability fix is
orthogonal to writer diversity and an untested change should not ride along with a
pilot report. No local vLLM writer was attempted (see `docs/WRITER-DIVERSITY.md`'s
"What was not done" for why); `qc filler`'s per-writer numbers are diluted by each
arm's much larger pre-existing luna-authored content, since the detector scans a whole
store rather than only this pilot's additions. `data/core-store` and the main
checkout's `runs/` were never touched.

## D-64 (2026-09-03) — Writer rotation Round 2: gemini-3.8-flash, two free OpenRouter models, and the D-53 schema fix diagnosed

**Context.** D-63 shipped a five-writer pilot and an 80/20 `gpt-5.6-luna`/
`claude-haiku-4-5` rotation recommendation, but recorded two open items without
investigating them: `gemini-3.7-flash` failed 100% of task (b) (D-53 per-sense
examples) on an unexplained `400 INVALID_ARGUMENT`, and the pilot's own attribution
number was confounded by uneven per-arm topic coverage. Google published
`gemini-3.8-flash` 2026-09-01; this round tests whether it (and two free OpenRouter
reasoning models new to the catalogue, `z-ai/glm-5.2:free` and
`nvidia/nemotron-3-super-120b-a12b:free`) change the rotation recommendation, and
closes both open items.

**Decision.**

1. **Diagnosed the D-63 Gemini schema failure precisely, live.** Bisecting
   `list[DraftSenseExample]`'s declared `maxItems` against `gemini-3.8-flash`
   (reproduces identically on `gemini-3.7-flash`) found Gemini's structured-output
   compiler rejects the schema once its *encoded weight* (item schema size times
   declared array bound) crosses an internal budget, not any single field or nesting
   depth: a bare `output_type=` request tolerates up to 54 (real contract) or 97 (a
   `str`-only reconstruction of the same shape); the real call shape,
   `NativeOutput(strict=True)` (what `stages.py` actually sends, confirmed by a second,
   corrective bisection after the first one's chosen fix still failed against real
   entries), tolerates only up to **32**. `contracts.MAX_EXAMPLE_SENTENCES` lowered
   200 -> 32 to make the D-53 schema callable on Gemini at all. **This is a
   pilot-scoped compromise, stated as such in the constant's own docstring, not a value
   recommended for production**: entries needing more than four live senses (22 of the
   300 pilot sample entries, 7.3%) now fail `DraftExampleBatch` validation for every
   writer, not only Gemini, since the ceiling is shared, non-provider-specific code.
   The correct fix — schema reshaping or call-splitting keyed on the active provider —
   was scoped but not built.
2. **Three price rows added** (`pricing.py`): `gemini-3.8-flash` ($0.75 in / $3.75 out
   per M, matching `gemini-3.7-flash`'s existing rate, cross-checked against the
   OpenRouter catalogue fetched 2026-09-03), `z-ai/glm-5.2:free` and
   `nvidia/nemotron-3-super-120b-a12b:free` (both $0, same catalogue fetch), each row
   commented with the free tier's data-sharing/rate-cap caveat.
3. **`scripts/run_writer_pilot.py` gained `--requests-per-minute` and `--max-tokens`**
   for the two free arms' rate caps and reasoning-blowup protection (neither was
   exercised — see below), and its `WRITERS` dict gained the three new arms.
4. **Found, and recorded rather than fixed, a pre-existing price-gate bug**:
   `ModelPolicy._all_model_ids`/`pricing.price_for` both naively split a model id on
   its first colon, assuming that colon is always this project's own routing-prefix
   separator; `router._split_model` already guards this correctly (checks the prefix
   is a recognised provider kind) but the price-gate call sites don't, so a bare
   OpenRouter id carrying the catalogue's own `":free"` suffix without an explicit
   `openrouter:` prefix is mis-split and wrongly refused. Orthogonal to writer
   diversity; covered by a regression test, not fixed, matching D-63's own standard for
   the Anthropic-529-retry gap it found and left (since fixed independently, see below).

**Pilot (`data/sample-writers/`, same 300 entries/seed 11, never `data/core-store`,
run from worktree `retrieval/writers2` off `retrieval/integration`).** Full tables,
the exact bisection numbers, and the recommendation are appended to
`docs/WRITER-DIVERSITY.md` as "Round 2". Summary here:

* **`z-ai/glm-5.2:free` and `nvidia/nemotron-3-super-120b-a12b:free`: 100% failure,
  both tasks, $0 spent, before any HTTP request** —
  `pydantic_ai.exceptions.UserError: Native structured output is not supported by this
  model.`, pydantic-ai's OpenRouter model-profile registry refusing
  `NativeOutput(strict=True)` for both ids, exactly like D-63's
  `deepseek/deepseek-v4-pro`. Neither arm's rate cap was ever exercised, since no
  request reached OpenRouter's servers; no daily-cap error was seen.
* **`gemini-3.8-flash` clears the D-53 schema failure (with the fix above) but is
  costlier and less reliable than `gemini-3.7-flash`'s already-flagged verbosity
  problem**: task (a) mean 1,221 output tokens/call (vs. D-63's 763.5 for
  `gemini-3.7-flash`); task (b) mean 3,714, up to 6,830, causing a **10% hard-failure
  rate** (5/50 entries) from `max_tokens` truncation on nothing more demanding than a
  6-sense entry. Judge mean score 64.92 (107 senses, $3.093587, 0/40 entries failed —
  the zero-529-failures gap from D-63's 5-7/40 is the `_RETRYABLE_STATUS` fix below,
  not this arm), the highest of any writer in either round but within the same
  62.8-64.6 noise band. Lowest any-flag rate of any writer measured (0.09%, 1/1,164)
  and perfect headword anchoring (100.0%).
* **Attribution re-measured on a topic-matched subset, closing D-63's confound**:
  intersecting the headwords every attributable arm (`luna`, `haiku`,
  `gemini-3.7-flash`, `qwen`, `gemini-3.8-flash`) actually covered yields 27 headwords;
  restricting to that subset drops accuracy from 52.73% (unmatched, full coverage,
  n=517/writer) to **38.64%** (matched, n=280/writer) against 20% chance for 5 writers —
  still well above chance, but confirming a large share of the unmatched number was
  topic leakage from uneven per-arm alphabetic coverage, as D-63 suspected but did not
  check.
* **Recommendation unchanged from D-63**: ship the 80/20 `gpt-5.6-luna`/
  `claude-haiku-4-5` rotation. `gemini-3.8-flash` is a research data point, not a
  production candidate, until its `max_tokens` truncation and the
  `MAX_EXAMPLE_SENTENCES` regression get a provider-aware fix. Neither free OpenRouter
  model is usable with this pipeline at all without a tool-call-based output-constraint
  mode, out of scope here as it was for `deepseek` in D-63.

**Consequence.** New: none (Round 2 reuses every D-63 script; `writer_diversity_report.py`'s
`_ARMS` tuple extended with `google`/`glm`/`nemotron`, not otherwise changed).
Modified: `contracts.py` (`MAX_EXAMPLE_SENTENCES` 200 -> 32, with the full diagnosis in
its docstring), `pricing.py` (three rows), `scripts/run_writer_pilot.py` (`WRITERS`
dict, `--requests-per-minute`/`--max-tokens` flags), `tests/test_writers.py` (+5:
three new price-gate coverage tests, the `MAX_EXAMPLE_SENTENCES` regression guard, and
the bare-`:free`-id price-gate-bug regression test).

**Left undone.** The provider-aware schema fix (split a many-sense entry's D-53 call,
or reshape the schema, per active writer) that would let `MAX_EXAMPLE_SENTENCES`
return to a value that does not regress non-Gemini writers on high-sense entries. A
tool-call-based (`ToolOutput`) integration for either free OpenRouter model, which
would be needed even to attempt measuring them. The `ModelPolicy._all_model_ids`/
`pricing.price_for` first-colon-splitting bug (found this round, recorded as a test,
not fixed — orthogonal to writer diversity). `data/core-store` and the main checkout's
`runs/` were never touched; this round's own worktree (`opengloss-wt-writers2`) is
separate from D-63's (`opengloss-wt-writers`).

## D-67 (2026-09-03) — Domain-retag pilot: `gpt-5.6-luna` cuts `domain_fits` defects and costs less than `gpt-5.4-nano`

**Context.** QA-DIARY iterations 5-6 found the Opus judge marking `domain_fits` as
defective on 29% of tier-2 senses (40 entries, 98 senses, seed 7) — the single largest
remaining defect after `relations_valid`. Domain tags were assigned by the `tag_domain`
retrofit pass on `gpt-5.4-nano`. This pilot asks whether re-tagging with `gpt-5.6-luna`
(the model D-55 and D-63 already prefer for other stages on cost and quality) fixes it,
without touching `data/core-store`.

**D-46's retag mechanism does not reach most of the defect.** `_clear_weak_domains`
only clears a sense's domain when it is the root's `.general` catch-all *and* was
tagged under a stale `TAXONOMY_VERSION` — bumping the version alone re-tags only that
slice. On this pilot's 98 live senses, only **15 (15.3%)** were `.general`; the other
84.7% already carried a specific leaf a plain version bump would never touch, yet the
judge flags 22-29% of all senses. D-46's mechanism structurally cannot fix most of this
defect. Per the task's own fallback clause, two small additive knobs were added to
`workflows/retrofit.py` / `cli.py` (`opengloss retrofit`), both off by default and
covered by 7 new tests in `tests/test_retrofit.py`:

* `--taxonomy-version` overrides the version `hygiene`/`tag_domain` compare against and
  stamp, without editing the `TAXONOMY_VERSION` module constant (`run_retrofit`,
  `_hygiene_pass`, `_tag_domain_pass`, `_clear_weak_domains`, `_tagged_under_current_taxonomy`,
  `_taxonomy_version_note`, `_tag_entry` all gained a `taxonomy_version` parameter,
  threaded via `functools.partial` in `run_retrofit`'s pass dispatch).
* `--force-retag-domains` clears **every** live sense's domain in the hygiene pass, not
  only weak `.general` ones (`_clear_weak_domains` gained a `force_all` flag). This is
  the flag actually used for the pilot's "every live sense" leg; the version-bump path
  was tried first and confirmed to under-cover (15/98), so it was not used alone.

**A second gotcha, matching the QA-DIARY finding "a nested env override replaces a
whole policy":** the same is true of a *partial* TOML `[policies.tag_domain]` table —
`AppConfig.policies` is validated as one dict field, so a config file naming only one
stage fails `_every_stage_has_a_policy` for every other stage. The supported way to
override one stage's model via `--config` is a TOML file carrying the **complete**
default policy set with only the target stage changed (generated from
`config._default_policies()`, not hand-typed) — same conclusion for the `OPENGLOSS_POLICIES__*`
nested-env route. `scripts/pilot-luna-tag-domain.toml` is that file for this pilot.

**Pilot design.** Worktree `opengloss-wt-retag` off `main` (`git worktree add`), never
touching `data/core-store`. The judge's 40-entry sample was reproduced exactly via
`opengloss qa --dry-run --from-list data/core/tier2_50k.tsv --sample 40 --seed 7 --store
<prod core-store>` (same 40 headwords, 98 live senses as QA-DIARY iterations 5-6). Those
40 entries were copied (`scripts/_pilot_copy_sample.py`, using `LexemeStore.read`/`write`,
never the production store's `write`) into two scratch stores: `data/sample-retag-nano`
(untouched control) and `data/sample-retag-luna` (treatment). On the treatment copy:

1. `opengloss retrofit --only hygiene --store data/sample-retag-luna --force-retag-domains`
   — 98/98 live senses cleared, **$0, 0 calls** (the copied entries already carry a
   `hygiene` marker, so step (c)'s rewrite call never fires; clearing domains is pure
   Python).
2. `opengloss retrofit --only tag_domain --store data/sample-retag-luna --config
   scripts/pilot-luna-tag-domain.toml` — re-tags all 98 senses with `gpt-5.6-luna`.
3. Both copies re-judged with the same Opus QA stage, forced: `opengloss qa --from-list
   data/core/tier2_50k.tsv --sample 40 --seed 7 --force --budget 5 --store <copy>`.

**Ledger — the retag call itself.**

| | model | calls | senses tagged | cost | cost/sense | input tok (cached) | output tok |
|---|---|---|---|---|---|---|---|
| control (historical, from copied provenance) | gpt-5.4-nano | 38 | 98 | $0.005241 | $0.0000535 | 132,055 (107,008) | 2,666 |
| treatment (this pilot) | gpt-5.6-luna | 40 | 98 | $0.003832 | $0.0000391 | 138,654 (128,136) | 2,498 |

Luna is **27% cheaper per sense** than nano on this stage — the same direction as D-55
(`queries`) and D-63/D-64 (writer rotation): reasoning tokens billed as output make nano
more expensive despite its lower headline price. (The nano row is not a fresh call —
re-billing it would have doubled judge-adjacent spend for a number already on disk in
every copied entry's `tag_domain` provenance.)

**Domain changes.** 45 of 98 live senses (45.9%) changed `root.leaf` under the retag.
Top 15 (old → new):

| count | old | new |
|---|---|---|
| 2 | people_society.emotion_attitude | people_society.character_traits |
| 2 | everyday_life.general | everyday_life.actions_routines |
| 2 | humanities.literature | language.rhetoric |
| 2 | nature.animals | science.biology |
| 1 | people_society.emotion_attitude | business.value_quality |
| 1 | business.value_quality | people_society.social_roles |
| 1 | technology.engineering | science.chemistry |
| 1 | technology.device_operation | nature.animals |
| 1 | technology.device_operation | technology.hardware_devices |
| 1 | people_society.emotion_attitude | people_society.social_issues |
| 1 | people_society.general | people_society.personal_names |
| 1 | sports_recreation.track_field | sports_recreation.general |
| 1 | sports_recreation.track_field | arts.dance |
| 1 | law_government.elections_politics | business.management |
| 1 | law_government.civics | business.management |

Full list in `scripts/_pilot_diff_domains.py`'s output. Only 15/98 were `.general`
before the retag, so most of the 45 changes are specific-leaf-to-specific-leaf
corrections D-46's mechanism would never have attempted at all — e.g. `beryllium`
(noun, "a light, brittle metal") moved `technology.engineering` → `science.chemistry`,
and `comet` (noun, the astronomical body) moved `nature.general` → `science.astronomy`.

**Judge before/after, same 40 entries/98 senses/seed 7, forced re-judge in this session
(claude-opus-5, $3.36 / $3.18):**

| sense-level defect rate | nano (control, fresh re-judge) | luna (treatment) |
|---|---|---|
| domain_fits | **22.45%** | **14.29%** |
| distinct_from_other_senses | 11.22% | 10.20% |
| examples_fit_sense | 35.71% | 35.71% |
| examples_natural | 38.78% | 33.67% |
| gloss_accurate | 11.22% | 13.27% |
| relations_valid | 81.63% | 85.71% |
| mean score | 68.3 | 68.78 |
| entries <60 / 60-79 / 80-89 | 7 / 30 / 3 | 6 / 32 / 2 |

Note the control's fresh `domain_fits` draw (22.45%) differs from iteration 6's
historical 29% on the identical 98 senses — the judge is a stochastic model, not a
deterministic check, so the two are separate draws. The valid comparison is the two
rows above, judged in the same session under identical rubric and sampling. `relations_valid`
and `examples_natural` moved 4 and 5 points respectively on **identical underlying
relation and example data** (this pilot only changed `domain`), which calibrates the
judge's own noise band on this sample size; `domain_fits`' 8.16-point move is the
largest of any metric and in the direction the retag predicts, `gloss_accurate` and
`relations_valid` moved slightly the other way (both within that noise band).

**Extrapolation to the whole store (110,869 live senses, QA-DIARY iteration-5 whole-store
scan) — pilot per-sense cost × count, not a measurement:**

| | luna | nano |
|---|---|---|
| extrapolated full-store re-tag cost | **$4.34** | $5.93 |

This is a *full* re-tag of every live sense (this pilot's `--force-retag-domains`), not
D-46's narrower `.general`-only sweep (QA-DIARY's earlier ~$1-at-nano estimate was for
that narrower sweep, on the 10K core, and is not comparable).

**Recommendation: retag the whole store with `gpt-5.6-luna`**, using
`--force-retag-domains` rather than a plain `TAXONOMY_VERSION` bump — a bump alone would
reach only the 15.3% `.general` slice this pilot measured, leaving most of the
`domain_fits` defect (specific-but-wrong leaves) untouched. Full-store cost ($4.34,
extrapolated) is lower than nano's equivalent ($5.93) and lower than the ≈$3.36 spent on
*one* 40-entry judge sample, for a measured 8-point absolute (36% relative) reduction in
the judge's `domain_fits` defect rate with no measured regression outside the judge's own
noise band. A `.general`-only retag (D-46's existing path) was not pursued further: it
would leave 84.7% of this pilot's changed senses (and, by extension, most of the
store-wide defect) untouched for a fraction of the saving.

**Total pilot spend:** $6.537659 (nano judge $3.358752 + luna judge $3.175075 + luna
retag $0.003832), against a $12 cap; no nano judge or retag calls were re-billed beyond
what was already on disk.

**Left undone.** The whole-store retag itself was not run (out of scope for a pilot;
`data/core-store` was never touched, per instructions). A fresh, larger post-retag QA
sample after any real run, to confirm the 40-entry pilot's 8-point move holds at scale
and is not itself a lucky draw — the judge's own noise band measured here (4-5 points on
untouched metrics) means an 8-point single-sample move is suggestive, not conclusive.

**Consequence.** New: `workflows/retrofit.py` (`taxonomy_version` and
`force_retag_domains` parameters on `run_retrofit`, `_hygiene_pass`, `_tag_domain_pass`,
`_clean_entry`, `_clear_weak_domains`, `_tag_entry`; `_taxonomy_version_note` and
`_tagged_under_current_taxonomy` take an explicit version), `cli.py` (`--taxonomy-version`,
`--force-retag-domains` on `opengloss retrofit`), `tests/test_retrofit.py` (+7). Pilot
scripts (`scripts/_pilot_copy_sample.py`, `scripts/_pilot_diff_domains.py`,
`scripts/pilot-luna-tag-domain.toml`, `scripts/pilot-nano-qa-report.json`,
`scripts/pilot-luna-qa-report.json`) are not wired into the CLI, following the D-63/D-64
convention for one-off pilot tooling. `ruff check`/`ruff format --check`/`ty check`/`pytest`
(1023 passed, 2 skipped) all clean. Both new flags default to the pre-D-67 behaviour
exactly, so an ordinary `retrofit` sweep's cost and output are unchanged.

## D-65 (2026-09-03) — `relation-reconcile`: the demotions the judge still reads, the pairs that disagree, and a cap

**Context.** D-50 gave relation edits to `relation_hygiene`, and gave it one rule: *nothing
is ever deleted*. A relation that fails a check is demoted to `see_also` with the reason on
`Relation.note`. That was right for the judgement, and it left two defects in the one thing
downstream readers actually consume, `Sense.relations`.

First, **the QA judge still reads every demoted edge.** `workflows/qa.py` renders each
relation on a sense as `type->term` — every one, `see_also` included — and asks whether the
sense's relations are valid. A `see_also` carrying `demoted: nano invalid` is precisely the
edge an earlier pass agreed was wrong, and it is still in the list the judge is shown. The
validity pass demoted ≈430K edges across the core store and `relations_valid` stayed false
on 84% of judged senses, because from inside the prompt nothing had changed. Nothing had:
the demotion is a note, and the note is not rendered.

Second, **the two halves of a symmetric pair disagree.** `validity`'s verdicts are
directional — one call judges `A --synonym--> B`, another judges `B --synonym--> A` — and
they regularly differ. Measured over the whole store (41,886 entries), synonym reciprocity
fell 98.4% → 93.7% and antonym 99.7% → 96.9% after the validity passes: ≈4,400 synonym
edges asserted on one side with the reverse demoted on the other. D-50's own far-side phase
repairs the demotions *it* makes; it cannot repair a disagreement between two verdicts it
made deliberately. And the store's third measured fact is that nothing anywhere bounds a
relation list: mean 13.4 relations per sense, median 10, p90 22, with see_also 49% (almost
all of it demotion residue), synonym 17%, hypernym 11%, antonym 11%, hyponym 9%.

**Decision.** A new `workflows/relation_reconcile.py`, three steps selectable through
`only=`, all **free** — no model call anywhere in the module — and all idempotent.

1. `asymmetric` — where a live sense of `A` holds a live typed edge of a symmetric type
   (`synonym`, `antonym`, `confusable_with`) resolved to a sense of `B`, and `B` holds the
   reverse **already demoted** (a `see_also` toward `A` carrying a demotion note) with no
   live edge of that type back toward `A` anywhere in its live senses, the stricter of the
   two directional verdicts wins: the near side is demoted too, note
   `reconcile:asymmetric:<far sense id>`. Counted per type.
2. `tombstone` — removes from `Sense.relations` every `see_also` carrying a demotion note,
   i.e. every edge that was *not authored* as a `see_also`. This is what actually shortens
   the list the judge reads. An authored `see_also` (no demotion note) stays.
3. `cap` — per sense, per type, keeps at most `RelationCaps`' allowance (synonym 8, antonym
   4, hypernym 3, hyponym 8, instance_of 4, meronym/holonym 4, everything else 4, a frozen
   dataclass the caller can replace) and tombstones the overflow. Keep order: resolved
   targets before unresolved, then edges `validity` accepted (present, typed, not demoted)
   before never-judged ones, then original order.

**Nothing is deleted in the sense D-1 forbids.** Both removing steps write what they took
out to a provenance record, one per (sense, step): a header line naming the sense, then one
line per edge — `reconcile:tombstone: <type> -> <term> [<note>]`, `reconcile:cap:<type> ->
<term> [<note>]`. The type recorded is the type the edge carried **when it was removed**,
because that is what `identity.edge_id` is built from and therefore what a reader needs to
rebuild the D-1 edge id. The pre-demotion type is *not* recorded, because it is not
recoverable: `relation_hygiene`'s demotion notes name the reason, not the type they came
from (only `retyped: nano <old>→<new>` names one), and this pass does not invent history.
`Lexeme.contrasts` are keyed by edge id and deliberately not cross-checked against live
edges (D-62), so a contrast whose edge is tombstoned survives — which is the behaviour that
schema note was written for.

**Demotion notes are enumerated from the code, not guessed.** `DEMOTION_NOTE_PREFIXES`
imports `relation_hygiene`'s public constants — `FAR_SIDE_NOTE_PREFIX`,
`HEADWORD_INFLECTION_NOTE`, `HEADWORD_PHRASE_NOTE`, `META_LABEL_NOTE`, `NANO_INVALID_NOTE`,
`NANO_RETYPE_NOTE`, `SIBLING_INFLECTION_NOTE` — plus this pass's own
`ASYMMETRIC_NOTE_PREFIX` and the generic `"demoted: "`, which is not a guess either:
`graph_hygiene._asserted_pairs` tests exactly that prefix to recognise "a hygiene pass
judged this pair", and it is what covers `graph_hygiene`'s and `content_hygiene`'s own
demotions without importing from three modules. `NANO_RETYPE_NOTE` is in the list because
`validity`'s retype path can name `see_also` as the better type.

**Pairing is entry-level, and that is the correction that made the pass work.** The first
implementation matched a near-side edge against a demoted reverse *on the sense the near
side resolved to*, mirroring `relation_hygiene._is_far_side_of`. Measured on the sample it
made reciprocity **worse** (synonym 93.5% → 91.0%, antonym 97.9% → 92.1%): it matched only
14 of the 61 one-sided pairs, because D-52's sense merging leaves plenty of targets resolved
to senses that have since been retired, so the surviving reverse lives on a different sense
id. `audit._audit_reciprocity` and `graph_hygiene._asserted_pairs` are both entry-level for
that exact reason — "whether the other side made the matching claim anywhere in its own
senses, not whether one particular sense did" — and so is this pass. D-50's sense-level test
stays right where it is: it governs a *demotion*, a judgement about one sense pair; a cap is
a judgement about how long a list may be.

**Reciprocity is protected in both directions.** `graph_hygiene` step 4 is blocked from
re-creating a demoted pair by the `"demoted:"` note on the surviving `see_also` — which
`tombstone` deletes. That is safe only because `asymmetric` runs first, so after a full
sweep neither side of such a pair asserts anything symmetric and step 4 has nothing to infer
from; selecting `tombstone` without `asymmetric` logs a warning, and `RelationReconcileStep.ALL`
fixes the order whatever order `--only` lists. And once a whole entry has been capped, every
symmetric, resolved pair it no longer asserts **anywhere** queues a far-side removal, run as
a second pooled phase after the main sweep has fully drained (D-31 — no two entry locks at
once). That phase **takes no `stop_event` parameter at all**, for D-50's second amendment's
reason and by its mechanism: `run_pool`'s workers return before pulling their first item
once the event is set, so a phase given the event would silently do nothing and leave the
store asserting one half of a pair the sweep has just taken apart.

**One sweep, one lock hold, unlike `relation_hygiene`.** All three steps run inside one
handler under one hold of the entry's lock: they are free, and they are ordered (`tombstone`
must see what `asymmetric` demoted, `cap` must count what `tombstone` removed), so three
pooled sweeps would mean three read-modify-write cycles per entry to reach one state.
`asymmetric`'s far-side *input* is collected up front by `_collect_demoted_pairs`, a
read-only lock-free projection of the **whole** store — `graph_hygiene._load_view`'s and
`audit_store`'s discipline — never restricted by `lexeme_ids`, because the far side of an
edge on a named list is very often not on it. The index holds only pairs whose far target is
itself an entry, which is what keeps it small (1,061 pairs over the 300-entry sample).

**The marker.** D-47's shape without an attempt counter: `relation_reconcile:<digest>` on a
zero-cost provenance record, the digest over the selected step names together with the
entry's live edge ids **as the sweep leaves them**. The steps are in the digest because a
marker written by `--only tombstone` must not stop a later full sweep from capping the same
entry. Written only on entries the sweep changed, so a store does not grow a record per
entry per sweep, and *refreshed but never created* by the far-side phase — an entry that
phase reaches may never have been swept itself, and a marker there would make a later sweep
skip an entry no step has run over. All three steps are idempotent by construction anyway
(a demoted edge is a `see_also`, a tombstoned edge is gone, a capped type is at its cap), so
the marker is a skip, not the mechanism. It keys on the near side only, so an entry whose
*far* side moves later is skipped by digest; `--from-list` names the remainder, the same
answer `relation-hygiene` has.

**A latent bug found on the way, recorded not fixed.** `_latest_marker` here orders
provenance records by the integer in their `p<n>` key, not by table order, because
`LexemeStore.write` serialises with `orjson.OPT_SORT_KEYS`: an entry with a hundred records
reads back `p1, p10, p100, p101, p11, …`, so "the last matching record is the most recent"
is false past 99 records. It was found because one sample entry (`foreground`, 106 records)
was re-examined on every rerun. `relation_hygiene._latest_marker` and `content_hygiene`'s
equivalent make exactly that assumption and are wrong on the same entries — their markers
are their own passes' business and another editor holds those files, so this is recorded
here rather than patched across three modules. A test covers the fix in this one.

**Measured, on a frozen 300-entry sample** (`scripts/build_sample_reconcile.py`, seed 65,
copied read-only from `data/core-store` into the worktree's `data/sample-reconcile/`). The
sample is a **breadth-first neighbourhood of the resolved graph**, not an independent draw:
every number this pass is measured by is a fact about a *pair* of entries, and 300 entries
drawn independently out of 41,886 share almost no edges. The consequence is a denser sample
than the store average — 1,043 live senses, 23,378 relations, mean 22.4 per sense (store-wide
13.4), max 1,772 on one sense — so its cap counts are an upper bound, not a store estimate.
Every `see_also` in the sample (12,332 of them, 53% of all edges) carried a demotion note;
not one was authored.

| | before | after |
|---|---|---|
| relations, total | 23,378 | 7,394 (−68%) |
| relations per sense, mean / median / p90 / max | 22.4 / 12 / 39 / 1772 | 7.1 / 6 / 13 / 24 |
| synonym reciprocity (`opengloss audit`) | 589/630 = **93.49%** | 480/480 = **100.0%** |
| antonym reciprocity | 546/558 = **97.85%** | 190/190 = **100.0%** |

One sweep, 300 entries, ~3 s, $0: `asymmetric` demoted **53** (synonym 41, antonym 12);
`tombstone` removed **12,385** `see_also` (the 12,332 already there plus the 53 just
demoted); `cap` removed **3,599** (antonym 2,280, synonym 848, instance_of 214, hyponym 142,
hypernym 112, entails 3) across **209** senses, of which **230** were far-side removals.
A second sweep: 0 changed, 299/300 skipped by marker (the 300th was never changed, so it was
never marked). `graph-hygiene` afterwards: **0 entries changed, 0 reciprocals added** — the
reconcile leaves it nothing to repair, and it undoes nothing the reconcile did.

**Consequence.** New: `src/opengloss_generator/workflows/relation_reconcile.py`,
`tests/test_relation_reconcile.py` (+36, offline), `scripts/build_sample_reconcile.py`.
Modified: `cli.py` (`opengloss relation-reconcile --store S [--from-list L] [--only ...]
[--dry-run] --concurrency C`, a real dry run that computes every edit and writes nothing,
`graph-hygiene`'s convention rather than the model passes' "stop before starting"), one
README row. Nothing else in the package changes, and nothing here touches
`data/core-store`. Run order: `relation-hygiene`, then this, then `graph-hygiene` to
confirm. **Left undone:** the QA judge is not re-run here — the claim that shortening the
list moves `relations_valid` is a prediction this pass makes cheap to test, not one it
tests; wiring a `RetrofitPass` member is left to whoever next touches `retrofit.py`; and the
`_latest_marker` ordering bug in `relation_hygiene` / `content_hygiene` is recorded above
and not fixed.

## D-66 (2026-09-03) — `qc filler` thresholds calibrated for examples, and a `filler_examples` rewrite pass

**Context.** D-60 shipped `qc filler` at the plan's own thresholds (4-grams > 0.05% of
sentences, openers > 0.5%, both scanned together across examples *and* encyclopedia
text) and measured, on `data/sample-300`, that those thresholds flagged 92.3% of
encyclopedia renditions but only 1.2% of example renditions — evidence the two fields
need different bars, left explicitly as future work ("a `--flag` run's candidate set
should be spot-checked before it drives a rewrite pass, not trusted blind"). Separately,
`docs/QA-DIARY.md` iteration 6's tier-2 re-judge put `examples_natural`'s defect rate at
42%, with the judge's recurring complaint "corpus-style research prose" — the same
symptom `stilted_examples` (D-49) already treats for a *fixed* regex
(`researchers`/`participants`/`the study`), but not for the phrases nobody wrote a regex
for. This closes both: a `--fields` option that lets `qc filler` measure examples on
their own denominator, thresholds calibrated against the full production store at that
scope, and a `content_hygiene` step that rewrites what the detector flags.

**Decision.**

1. **`--fields examples|encyclopedia|all`, and it changes more than what gets reported.**
   `analyze_filler(store, *, fields="all")` (`qc/filler.py`) now filters
   `_collect_refs`'s output *before* pass 1's sentence-level counting, not after: since a
   key's frequency is `(sentences containing it) / (total sentences scanned)`, restricting
   the scope changes the denominator every threshold is measured against, which is what
   lets an example-only scan surface tells the encyclopedia's own boilerplate was
   drowning out. `calibrate_thresholds` (same module) takes the same option, defaulting
   to `"examples"` since that is what this decision is calibrating. `qc filler`'s own
   `--fields` CLI option defaults to `"examples"` too — a change from D-60's implicit
   "always all fields" — while `analyze_filler`'s own default stays `"all"`, so any
   existing caller that does not pass `fields` keeps D-60's behavior.

2. **The calibration (`qc filler-calibrate`, new command; `calibrate_thresholds`, new
   function).** Pass 1 (sentence-level n-gram/opener counting) does not depend on the
   threshold at all, so it runs once regardless of how many `(ngram_freq_threshold,
   opener_freq_threshold, min_count)` triples are measured — only pass 2 (which keys
   clear the bar) reruns per triple, which is what makes a multi-point sweep over a
   production store's 41,918 entries practical. Read-only against
   `/home/mjbommar/projects/personal/opengloss-generator/data/core-store` (this feature's
   own worktree only ever writes to `data/sample-filler`), `--fields examples`:

   | ngram threshold | opener threshold | min_count | renditions flagged | flag rate |
   |---|---|---|---|---|
   | 0.005% | 0.05% | 5 | 237,868 | 19.36% |
   | 0.01% | 0.1% | 5 | 140,797 | 11.46% |
   | 0.025% | 0.25% | 5 | 80,055 | **6.52%** |
   | 0.05% (D-60's plan default) | 0.5% (D-60's plan default) | 5 | 45,217 | 3.68% |
   | 0.1% | 1% | 5 | 0 | 0.00% |
   | 0.2% | 2% | 5 | 0 | 0.00% |
   | 0.5% | 4% | 5 | 0 | 0.00% |

   (1,228,673 example renditions, 1,236,750 sentences scanned, held constant across every
   row.) D-60's own plan-default thresholds already land inside the plan's "3-8%" target
   once scoped to examples alone (3.68%) — the field-scoping in point 1 was most of the
   fix by itself — but 0.025%/0.25% lands nearer the middle of the band (6.52%) with a
   richer, more legible top-25 (below) rather than six near-identical two-word openers, so
   that pair is `FillerConfig`'s new default (`min_count` unchanged at 5).
   Top 25 phrases at the chosen defaults, by sentence count:

   ```
    1. "after the"                    (opener, 2, 10841, 0.877%)
    2. "in the"                       (opener, 2,  9125, 0.738%)
    3. "during the"                   (opener, 2,  8881, 0.718%)
    4. "at the"                       (opener, 2,  8539, 0.690%)
    5. "the museum"                   (opener, 2,  7834, 0.633%)
    6. "the committee"                (opener, 2,  5167, 0.418%)
    7. "the coach"                    (opener, 2,  4766, 0.385%)
    8. "our class"                    (opener, 2,  3963, 0.320%)
    9. "the teacher"                  (opener, 2,  3872, 0.313%)
   10. "the report"                   (opener, 2,  3614, 0.292%)
   11. "the school"                   (opener, 2,  3463, 0.280%)
   12. "the old"                      (opener, 2,  3362, 0.272%)
   13. "the team"                     (opener, 2,  3353, 0.271%)
   14. "the museum displayed a"       (4-gram, 4,  1221, 0.099%)
   15. "after the storm the"          (4-gram, 4,   465, 0.038%)
   16. "her name on the"              (4-gram, 4,   449, 0.036%)
   17. "as a hereditary surname"      (4-gram, 4,   444, 0.036%)
   18. "the end of the"               (4-gram, 4,   433, 0.035%)
   19. "on the birthday card"         (4-gram, 4,   381, 0.031%)
   20. "for the school play"          (4-gram, 4,   379, 0.031%)
   21. "attracts visitors with its"   (4-gram, 4,   369, 0.030%)
   22. "given name on the"            (4-gram, 4,   351, 0.028%)
   23. "name on the birthday"         (4-gram, 4,   344, 0.028%)
   24. "wrote her name on"            (4-gram, 4,   333, 0.027%)
   25. "his name on the"              (4-gram, 4,   320, 0.026%)
   ```

   The 4-grams (14-25) are unmistakable generation crutches — "the museum displayed a
   ___", a birthday-card/given-name template repeated across unrelated headwords, "as a
   hereditary surname" reused wholesale. The 2-word openers (1-13) read as a smaller,
   subtler tell at first glance ("after the", "in the" are common English bigrams on
   their own), but their concentration is the finding: 0.877% of *every example sentence
   in the store, regardless of headword* opens "After the ...", which is a model reaching
   for the same sentence frame dictionary-wide, not a property of English. The
   institutional-subject cluster (5-13: "the museum"/"the committee"/"the coach"/"our
   class"/"the teacher"/"the report"/"the school"/"the team") is the same habit from the
   other end — a small, fixed set of scene-setting nouns standing in for whatever the
   headword's sentence actually needs a subject for.

3. **`filler_examples`, an eighth `content_hygiene` step.** Every example rendition of a
   live sense carrying `QAFlag.OG_FILLER` — any reading level or register, unlike
   `stilted_examples`' canonical-only scope, since the flag is a corpus-wide signal about
   one piece of text rather than a rule about the canonical field — gets one luna call per
   entry, reusing `stilted_examples`' prompt shape (same `_EXAMPLE_FIELD_RULE` slice) plus
   one addition neither sibling step needs: the specific phrase a fresh corpus scan found
   the rendition carrying, named as the one thing the rewrite must not reuse
   (`qc.filler.phrases_in`, new function, checked against a `FillerReport` taken once per
   step run rather than once per entry — pass 1's counting is store-wide and does not
   change between entries in the same sweep). A rewrite is adopted only when
   `spans.find_span` can still place the headword (D-45's non-negotiable) and it does not
   collide with a sibling example at the same `(level, register)` key
   (`workflows/example_hygiene.py`'s own `_collides`, not `stilted_examples`' canonical-only
   check, since offenders here are not all canonical) — the store's uniqueness rule would
   reject the entry on its next read otherwise. An adopted rewrite clears `OG_FILLER`; a
   refused one leaves the old text and the flag exactly as they were, D-47's marker
   (`content_hygiene:filler_examples:<digest>;attempts=<n>`) bounding retries at two per
   entry the same as every other model step in this module. Selectable via the existing
   `content-hygiene --only filler_examples`.

**Measured on `data/sample-filler`** (300 fresh entries, seed 66, rows 15,000-31,887 of
`tier2_50k.tsv` — disjoint from `build_sample_writers.py`'s window and sample; copied
read-only from the production store, never written to).

`qc filler --flag` (defaults: 0.025%/0.25%/5, `--fields examples`):

| | value |
|---|---|
| entries scanned | 300 |
| example renditions scanned (units) | 9,146 |
| sentences scanned | 9,204 |
| renditions flagged | 726 (7.94% of example renditions) |
| entries with >=1 flagged rendition | 227 / 300 |

`content-hygiene --only filler_examples --budget 1.00`:

| | value |
|---|---|
| entries scanned | 300 |
| entries changed | 227 |
| calls | 227 ($0.037779 total; **$0.0000534/rewrite**, $0.0001664/call) |
| rewritten (accepted) | 708 |
| refused | 18, all "rewrite dropped the headword" (0 sibling collisions) |
| cost per entry with an offender | $0.000167 |

708 + 18 = 726: every flagged rendition got exactly one attempt and a verdict, none left
over for a second sweep. A second run of the same command makes 0 calls and spends
$0 (D-47: no offending set changed).

**Before/after, read by hand (10 pairs, all 10 legible improvements or lateral, none
worse):**

- `levelness`: "Engineers **assessed the levelness of the** concrete floor before
  installing the precision equipment." -> "Engineers checked the concrete floor for
  levelness before placing the delicate equipment." — clearer and shorter, phrase gone.
- `disuse`: "The archive suffered mold growth as a direct consequence of disuse." ->
  "Prolonged disuse caused the irrigation system's pipes to corrode and fail." —
  concrete and varied; the flagged phrase (`the archive`, an opener) is gone along with
  the passive "as a direct consequence of" construction it wasn't even flagged for.
- `uncuff`: "**After the** courtroom rehearsal ended, the director uncuffed the actor." ->
  "The officer uncuffed the suspect once the judge dismissed the mistaken charge." —
  drops the formulaic "After the ..." opener and reads as an actual scene.
- `rosetta` / `rapunzel` / `massa` / `branco`: each swapped only its flagged opener
  ("our class", "in the", "the museum") for a different one; register and content are
  otherwise unchanged. Not worse, but a reminder that this pass targets one *specific*
  corpus-wide phrase per rewrite, not overall naturalness — `stilted_examples` already
  owns the broader "sounds academic" defect, and the two are complementary rather than
  redundant.

**Consequence.** `qc/filler.py`: `_VALID_FIELDS`, `_filter_fields`, `fields` parameter on
`analyze_filler` (reported in `totals.fields`), `phrases_in`, `CalibrationPoint`,
`calibrate_thresholds`; `FillerConfig`'s `ngram_freq_threshold`/`opener_freq_threshold`
defaults move `0.0005`/`0.005` -> `0.00025`/`0.0025` (D-60's numbers are still reachable
via explicit CLI flags). `cli.py`: `qc filler` gains `--fields` (default `examples`);
new `qc filler-calibrate --out FILE [--ngram-thresholds --opener-thresholds --min-counts
--fields --from-list --top-n]`, read-only, no model, no `RunSession` (mirrors
`export-triples`/`export-qrels`'s own free-command pattern). `workflows/content_hygiene.py`:
`ContentHygieneStep.FILLER_EXAMPLES`, `FILLER_EXAMPLE_NOTE`, `FILLER_EXAMPLES_INSTRUCTIONS`,
`_filler_report`/`_filler_examples`/`_build_filler_prompt`/`_filler_collides`/
`_apply_filler_rewrite`/`_rewrite_filler`/`_filler_examples_step`, wired into
`ContentHygieneStep.ALL` and `_STEP_FUNCTIONS`. README gains rows for `qc filler`
(updated) and `qc filler-calibrate` (new). Nothing here touches
`/home/mjbommar/projects/personal/opengloss-generator/data/core-store`; the pilot ran
only against this worktree's own `data/sample-filler`. Tests: **+12**
(`tests/test_qc_filler.py` +6 — `--fields` scope/union/rejection, `phrases_in` hit and
miss, `calibrate_thresholds`' flag rate and rejection; `tests/test_content_hygiene.py`
+6 — accept-and-clear, headword-dropped refusal (reusing `NO_SPAN_HEADWORD`), a
sibling-collision refusal built from two offenders scripted the same rewrite text so the
second collides with what the first just wrote, non-canonical-rendition coverage,
zero-cost when unflagged, and idempotence; `tests/conftest.py` gains one payload,
`_filler_rewrite_payload`, appended in the file's existing append-only block).
`uv run ruff check`/`format`, `uv run ty check`, `uv run pytest` (1029 passed, 2
pre-existing skips, up from 1017 passed on this branch's fork point) are clean on
`hygiene/filler-rewrite`.

**Left undone.** `filler_examples`' recomputed `FillerReport` (for naming the offending
phrase) is a fresh scan taken at the step's own default thresholds, not necessarily the
exact thresholds a prior `qc filler --flag --ngram-threshold ... --unflag` run used — a
mismatch only ever degrades to "no phrase named," never a wrong rewrite, but a
config-threading fix (carrying the flagging run's own config forward) would make the
"avoid" hint reliable under a non-default `--flag` invocation too. The `"encyclopedia"`
and `"all"` scopes still use the same calibrated-for-examples defaults; D-60 already
flagged the encyclopedia number as unvalidated and this decision does not revisit it — an
encyclopedia rewrite pass is out of scope here, as it was there.

## D-68 (2026-09-04) — `relation-reconcile --only verdicts`: the `contrasts` stage's verdicts, finally acted on

**Context.** D-57 built the `contrasts` stage and, on purpose, made it inert: it writes a
paragraph per relation edge, records a `ContrastVerdict` alongside, and changes nothing,
because D-50 gives relation edits to the hygiene passes and "a stage whose job is prose has
no business deleting an edge on the strength of a by-product". That left the verdicts
sitting in `Lexeme.contrasts` with no pass that reads them. D-57 called them "a work list
for relation hygiene that cost nothing extra" and then nobody worked the list. On the core
store the list is 1,458 contrasts on 271 entries, of which **445 (30.5%) say the edge is
not what it claims** — 428 `related_differently` and 17 `unrelated`. Those are model
verdicts already bought and already on disk.

**Decision.** A fourth free step in `relation_reconcile.py` (D-65), `verdicts`, first in
the step order. For each live sense, each contrast whose `edge_id` matches one of that
sense's edges:

1. **`unrelated` and `related_differently` both demote the edge to `see_also`**, with the
   note `demoted: contrast <verdict>`. The note deliberately uses the project-wide
   `"demoted: "` shape rather than this module's own `reconcile:asymmetric:` shape, so
   `is_demotion_note` recognises it through `_GENERIC_DEMOTION_PREFIX` with no new entry in
   `DEMOTION_NOTE_PREFIXES`, `graph_hygiene._asserted_pairs` reads it as "a pass judged this
   pair" for as long as the edge survives, and `relation_hygiene._far_side_reason` strips
   exactly that prefix to build the far-side note. No new convention was invented.

2. **`related_differently` is demoted, not retyped.** The paragraph says the two are
   related but not *as typed*; it does not say how, the contract never asked, and reading a
   new relation type out of prose written to answer a different question would be this pass
   asserting something no model was shown. `see_also` is the honest answer — the two are
   related, the relation is unnamed — and D-50's `validity` step is free to retype it later
   on a verdict of its own. The measurement below is what makes this the conservative call
   rather than the lazy one: most `related_differently` verdicts on this sample are a
   *synonym that should be a hypernym or hyponym*, and demoting loses the direction. The
   alternative loses more: guessing which of the two is the general term from a paragraph
   that was not asked to say.

3. **Far side, sense-level, in the existing second phase.** A contrast is written once per
   *undirected* pair (D-57 §1), so the reverse edge has no contrast of its own and nothing
   else will ever reach it. A demoted edge that was symmetric (synonym / antonym /
   confusable_with) and resolved to an entry in the store queues a `_FarSideDemotion`,
   applied after the main sweep has fully drained so no two entry locks are held at once
   (D-31), with **no stop event**, for the reason and by the mechanism D-50's second
   amendment gives. The far-side identity test is **sense-level** — the opposite of
   `cap`'s, which is entry-level and right for a cap. A cap is a judgement about how long a
   list may be; a contrast verdict is a judgement about *these two senses*, and the far
   entry may hold a perfectly good relation of the same type toward the same lexeme about a
   different sense. That is `relation_hygiene._is_far_side_of`'s argument, copied.

4. **`verdicts` runs FIRST, ahead of `asymmetric`.** It is a demoting step and every
   demotion it makes must be visible to `tombstone` in the same sweep, or the edge the
   `contrasts` stage rejected stays in the list the QA judge reads until somebody runs the
   pass twice — which is the exact defect D-65 exists to fix, reintroduced. Measured below:
   with `verdicts` first, all 272 near-side demotions were tombstoned in the same sweep.
   Nothing later in the order can undo it, since `asymmetric` only looks at live *typed*
   edges and a demoted one is a `see_also`.

5. **The marker digest gains the contrasts.** D-65's sentinel covered the selected step
   names and the entry's live edge ids. That is not enough now: a `contrasts` sweep adds
   verdicts *without touching a single relation*, so an entry reconciled before its
   contrasts were written has exactly the same edge ids afterwards, the digest matches, the
   entry is skipped and the verdicts are never applied — silently, forever. The digest now
   also covers one `contrast=<edge id>=<verdict>` ref per stored contrast, live edge or not
   (D-62 keeps a contrast whose edge has gone; dropping those would make the digest flicker
   as `tombstone` removes their edges). The verdict is in the ref as well as the key because
   a re-judged edge is a different instruction even though the key did not move.

**Two things this step deliberately does not do.** It does not prune `Lexeme.contrasts`
when it demotes their edges — D-62 is explicit that "a contrast whose edge has gone is
evidence about a removed relation rather than a validation error", and after `verdicts` and
`tombstone` have both run that is the common case, not the exception. And the far-side
phase writes **no marker** on the entry it demotes on, unlike `cap`'s far-side removal.
That is the difference between the two edits: a cap's removal leaves the entry finished,
whereas a verdict's demotion leaves behind exactly what `tombstone` exists to remove, and
this phase runs after `tombstone` has already visited that entry in this sweep. Refreshing
the marker would freeze the new digest and strand the demoted `see_also` in the judge's
list; leaving the stale one is what makes the next sweep re-examine the entry. The store
therefore converges in **two** sweeps rather than one, measured below.

**Measured (`data/sample-verdicts`, 2,908 entries, `--concurrency 8`, free).** The sample
is every contrast-bearing entry in the core store — the `contrasts` pilot ran over the
first 300 headwords of `data/core/tier2_50k.tsv` and **271** came back with paragraphs, so
it is 271 and not the round 300 the brief asked for; there are no others — plus the 2,637
entries their resolved edges point at, because the far side of a judged edge never carries
a contrast and the far-side demotions are not measurable without it
(`scripts/build_sample_verdicts.py`). Copied read-only; `data/core-store` untouched.

| | |
| --- | --- |
| Contrasts present | 1,458 on 271 entries |
| Verdicts | `related_as_typed` 1,013 · `related_differently` 428 · `unrelated` 17 |
| Contrasts still matching a live edge | 917 (62.9%) — `related_as_typed` 648, `related_differently` 260, `unrelated` 9 |
| Edges demoted, near side | **272** (`related_differently` 263, `unrelated` 9), on 269 distinct edge ids |
| By relation type, near side | synonym 241, antonym 31 |
| Far-side demotions | **222**, of 272 queued — the other 50 far entries hold no reverse edge of that type resolved to that sense |
| Demoted total, one sweep | 494 (`related_differently` 478, `unrelated` 16; synonym 437, antonym 57) |
| Tombstoned in the same sweep | 272 — every near-side demotion |
| Second sweep | tombstones the 222 far-side demotions, demotes 0; third sweep is a no-op (793 skipped) |
| Duration | 8.4 s for the full five-step sweep, $0 |

Two numbers are worth reading twice. **541 of 1,458 contrasts (37.1%) no longer match a
live edge at all** — the relation they were written about has already been demoted or
tombstoned by `relation_hygiene` and D-65's own steps in the weeks since. That is D-62's
"don't cross-check contrasts against live edges" rule earning its keep: those paragraphs are
still true and still readable, they are simply evidence about an edge the graph no longer
asserts. And a `--only verdicts` sweep reports 496 demotions where the full sweep reports
494: two reverse edges that the demoting half of the far-side phase would have demoted were
already removed by `dedup` or `cap` earlier in the same sweep. The edge is gone either way.

**The eyeball check: do these look like the right edges?** All 9 acted-on `unrelated`
verdicts and a seeded random 15 of the 260 distinct acted-on `related_differently` edges were read against
the paragraph the model wrote.

*All 9 `unrelated`.* Seven are unambiguously right and two are defensible:
`rushing:adjective:1-synonym->torrent` (adjective resolved to a noun),
`tug:verb:0-synonym->yank:noun:0` and `tug:verb:0-synonym->jerk:noun:0` (same defect
twice), `tug:noun:1-synonym->tugboat` (the *pull* sense of the noun resolved to the
*vessel* sense), `madam:noun:0-antonym->senhor` (cross-language: English against
Portuguese), `privileged:adjective:1-antonym->rootless:adjective:2` (a computing sense
against a social one), `decorate:verb:0-antonym->denuded:verb:2`. The two arguable ones are
`coma:noun:0-synonym->insensibility` and `riddle:noun:2-synonym->strainer`, where the two
words really are near neighbours and the paragraph argues a fine distinction; demoting to
`see_also` rather than deleting is exactly the right disposal for both — the cross-reference
survives, the false synonymy claim does not.

*15 of 260 `related_differently`.* Every one is a real defect, and **D-57's diagnosis does
not generalise.** That decision, reading 19 verdicts on a 300-entry slice, found "most are
the same real defect — a relation resolved to a sense of the wrong part of speech". Counted
over all 269 distinct acted-on edges here, a part-of-speech mismatch between the source sense and
the resolved target sense accounts for **75 (27.9%)**, not most: `cracking:verb:0-synonym->
fracture:noun:0`, `annotated:adjective:1-antonym->unmarked:verb:1`,
`catching:verb:2-synonym->draw:noun:3` in this sample of 15. The dominant pattern is
different and, for this step, more interesting: a **synonym that is really a hypernym or a
hyponym**. `falcon->peregrine` ("peregrine names a specific species… falcon is the broader
genus term"), `psychic->clairvoyant`, `examiner->investigator`, `preserving->archiving`,
`accusation->indictment`, `compulsory->required`, `dictator->pharaoh`,
`operative->shinobi`, `justification->legitimacy`, `militant->partisan`,
`penetrate->tunnel` — eleven of the fifteen. In every one the paragraph names which member
is the broader term, in prose, and the step throws that away by demoting to `see_also`.
That is the honest cost of decision 2 and it is written down here rather than hidden: the
information to retype these correctly exists, in the contrast text, and reading it back out
would need a model call this pass does not make. A `relation-hygiene` step that retypes a
`see_also` carrying `demoted: contrast related_differently` by reading its contrast
paragraph is the obvious next feature, and it is not this one.

The judgement, then: **yes, these are the edges a reader would agree with.** Nothing in the
24 read was an edge that should have been left alone; the disagreement is only ever about
whether `see_also` is generous enough, never about whether the original type was wrong.

**Consequence.** Modified: `src/opengloss_generator/workflows/relation_reconcile.py`
(`RelationReconcileStep.VERDICTS` first in `ALL`, `VERDICT_NOTE_PREFIX`, `_apply_verdicts`,
`_FarSideDemotion`/`_far_side_demotion`/`_is_far_side_demotion_of`/`_demote_far_side`/
`_demote_far_side_all`, `_contrast_refs` in the marker digest, `by_verdict` and
`far_side_demoted` on `RelationReconcileStepResult`, and the `tombstone`-without-a-demoting-
step warning extended to name `verdicts` as well as `asymmetric`); `cli.py` (the `--only`
help and the command docstring); `README.md` (the `relation-reconcile` row). New:
`scripts/build_sample_verdicts.py`. Tests: **+13** (`tests/test_relation_reconcile.py`:
each verdict's disposition, the far-side reverse demotion and its sense-level restraint, a
contrast keyed on another sense, unresolved and asymmetric edges queueing no far-side work,
idempotence, the contrast surviving its own edge, demote-then-tombstone in one sweep, the
far-side demotion tombstoned by the next sweep, and contrasts arriving after a sweep
reopening a marked entry), 1,086 pass. `data/core-store` untouched — the sample is a
read-only copy.

## D-69 (2026-09-04) — `qa-pairs`: two more free post-checks for D-58's own recorded defects — meta-reference leakage and gloss echo

**Context.** D-58's pilot shipped `qa-pairs` with three free post-checks and recorded two
more as "the highest-value next change" rather than fixing them on the spot: **7.9%** of
stored answers named the prompt's own scaffolding ("the example(s)", "according to the
sources", "the passage") despite an explicit ban in the instructions, and **11.6%** of
`definition` answers echoed the canonical gloss verbatim rather than restating it. Both
defects are checkable for free against text already on disk, which is exactly the shape
of check this stage exists to run.

**Decision.**

1. **`meta_reference(answer) -> str | None`** scans an answer against eight
   word-boundaried, case-insensitive patterns covering the shapes above (`"the
   example(s)"`, `"the passage"`, `"the text above/provided/given"`, `"according to the
   sources/text/example/passage/gloss/entry/definition"`, `"as described/stated/
   mentioned/shown in/above"`, `"in the example/passage/sources above/provided/given"`,
   `"the given/provided/supplied text/examples/sources"`, `"the sources"`), returning the
   phrase matched. Deliberately does not match `"for example"` or `"an example of"`: both
   name a rhetorical device, not the prompt's own supplied text, and no pattern fires on
   the bare word `"example"` without a preceding `"the"` or an enclosing frame.
2. **Repair before drop.** A leading clause naming the scaffolding — `"According to the
   sources, "`, `"As described in the example, "` and their kin, up to the first comma —
   is stripped for free and the answer re-checked. Only an answer that still names the
   scaffolding after that repair is dropped, as `meta_reference`; a repair that succeeds
   is counted separately (`meta_reference_repairs`) rather than folded into either the
   accepted or the dropped count, because it is neither a clean answer nor a rejected one.
3. **`echoes_gloss`.** A `definition` answer is dropped when its first 60 characters,
   casefolded and whitespace-collapsed, equal the canonical gloss's own first 60 — a
   verbatim-or-near-verbatim copy, checked against exactly the text the model was shown,
   never against a rendition rewritten since.
4. Both checks sit in `_judge`, after the existing citation and overlap checks and before
   the duplicate-question check, in the same "cheapest first, first failure wins" order
   the sieve already used. No retry, matching every other check in this stage: a dropped
   or repaired pair costs nothing extra because the next sense's call is already buying
   seven more.

**Measured (`data/sample-qameta`, 300 headwords copied read-only from
`../opengloss-generator/data/core-store`'s first 300 `tier2_50k.tsv` entries, which
already carried `qa-pairs` output from an earlier whole-store run — a different sample
from D-58's own `sample-300` pilot, so the rates below are an independent measurement,
not a reproduction).**

| measured | |
|---|---|
| QA pairs scanned | 6,090 |
| meta-reference matches | **116 (1.90%)** |
| — repairable by the leading-clause strip | 14 (12.1% of matches) |
| — would be dropped outright | 102 (1.67% of all pairs) |
| `definition` pairs scanned | 883 |
| `echoes_gloss` matches (first-60-char rule, exact) | **0 (0.00%)** |

The `echoes_gloss` rate on this sample is far below D-58's 11.6%, and reading the
`definition` answers explains why without impeaching the check: this sample's answers
routinely restate the gloss almost word-for-word but swap its opening frame (`"A
preposition indicating ..."` -> `"Aboard indicates ..."`), which changes the first 60
characters even though most of the sentence after them is an unchanged copy. The
first-60-character rule is deliberately the narrow, cheap-to-verify check specified for
this stage rather than a general near-duplicate detector; it catches a true nose-to-tail
echo and nothing subtler, and this sample's zero count is a real property of *this*
sample's answers, not evidence the check is broken.

**False-positive judgement.** All 116 meta-reference matches were read. 112 are
unambiguous true positives — the answer names "the example(s)", "the sources" or "the
passage" to point at the prompt's own supplied text, exactly the failure the check exists
to catch (e.g. `crab:noun:2`, comparison: *"The definition places it in the pubic region,
while the examples describe it living ... in body hair"*; `dent:noun:0`, hypothetical:
*"Yes, according to the sources, dents can be produced not only by impact but also by
pressure..."*). 4 are genuine false positives, and all 4 fall under the bare `"the
passage"` pattern fired against a headword whose own meaning is a physical passage:
`gorge:noun:1` answered three questions with "the passage" meaning the throat itself
("the passage in the upper digestive tract through which food passes"), and
`reinforce:verb:0` used it for a tunnel ("before the passage was reopened"). One further
case is borderline (`lengthy:adjective:0`: "the passage benefits from paraphrase" reads
as a generic statement about lengthy text rather than a pointer at the supplied
encyclopedia passage). No false positive occurred under any of the other seven pattern
families across the 108 remaining matches, and no idiom of the excluded shapes ("for
example", "an example of") was ever caught. Net: a ~3.4% false-positive rate concentrated
entirely in one pattern, on headwords whose ordinary sense collides with the scaffolding
vocabulary — recorded here rather than acted on, since the patterns shipped are exactly
those specified and narrowing `"the passage"` further (e.g. requiring `"above"`,
`"provided"` or `"given"` to follow it, as the other passage-shaped patterns already do)
is a follow-up worth measuring on its own before it is adopted.

**Consequence.** `src/opengloss_generator/workflows/qa_pairs.py`: `DropReason.
META_REFERENCE`, `DropReason.ECHOES_GLOSS`, `meta_reference()`, `GLOSS_ECHO_PREFIX_LENGTH`
(both exported), `_repair_meta_reference()`, `_echoes_gloss()`, `_echo_key()`; `_judge()`
now returns `(verdict, repaired: bool)` and `_sift()` a third element (repairs made);
`QAPairsOutcome.meta_reference_repairs`, reported in `as_dict()`. `tests/test_qa_pairs.py`
+8 (2 parametrized pattern tests covering the positive shapes and the "for example" /
"an example of" negatives, the repair path, the unrepairable drop, the gloss-echo drop,
and the summary carrying both new counters). `tests/conftest.py`: `_qa_pair_set_payload`'s
`definition`-type answer now gets an "In short, " lead-in rather than a bare quote, so a
generic scripted sense does not incidentally echo its own short gloss by fixture
construction rather than genuine model behaviour; three new scripted headwords
(`QA_GLOSS_ECHO_HEADWORD`, `QA_META_REFERENCE_HEADWORD`, `QA_META_REPAIR_HEADWORD`)
appended to the existing D-58 block. New: `scripts/qa_pairs_meta_audit.py`, a read-only,
zero-model-call measurement script (mirrors `scripts/near_copy_rate.py`'s own shape) for
re-running the measurement above against any store. `uv run ruff check`/`format`, `uv run
ty check`, `uv run pytest` (1,095 passed, 2 pre-existing skips, up from 1,073 at this
branch's fork point) are clean on `hygiene/qa-meta`. Untouched, per the branch's scope:
`cli.py`, `schema.py`, `config.py`, `prompts.py`, and the production
`../opengloss-generator/data/core-store` (this decision's own measurement store,
`data/sample-qameta`, lives only in this worktree).

**Left undone.** The `"the passage"` false positives above are a real, narrow gap: a
headword-aware exclusion (skip the check, or require a following "above/provided/given",
when the sense's own gloss is about a physical passage) would close it, but should be
measured on its own pilot rather than folded into this change sight-unseen. `procedural`
degrading into `factual` when a sense describes no procedure (D-58's third recorded
defect) is still untouched, and is not a regex-shaped fix in the first place.

## D-70 (2026-09-03) — `circular_gloss`, a ninth `content_hygiene` step, for definitions that define their own headword

**Context.** A read-only overnight scan of the live core store (41,886 entries) sampled
15,718 senses and found 2,040 (13.0%) whose canonical gloss uses the headword itself —
`stubborn:adjective:0` "Marked by a **stubborn** unwillingness to change…",
`lilting:noun:0` "…produced by **lilting** prosody". A circular definition is the
classic lexicographic defect, and it is worse than merely unhelpful for this project
specifically: the canonical gloss is used as the positive example for its own headword's
retrieval query (`workflows/queries.py`), so a circular gloss makes the query and its
positive share the headword verbatim — training the retriever to reward lexical overlap
with the query rather than semantic match. This closes it with a ninth
`content_hygiene` step, following the shape D-49/D-66 already established for that
module: free detection, one luna call per entry batching every offender, free
post-checks that refuse a rewrite rather than trust it blind.

**Decision.**

1. **Detection is free.** `_circular_glosses(entry)` (`workflows/content_hygiene.py`)
   runs `spans.find_span` (already the project's headword-in-text finder) over each live
   sense's canonical gloss, with `_forms_for(entry, pos_entry)` supplying candidates —
   the same morphology-inflected-forms-plus-`generate_forms` union `stilted_examples`,
   `fragment_examples` and `degenerate_renditions` already build for their own example
   and rendition checks (spans.py, "reuse it" was the brief, not "write a second one").
   This is what catches `lilt` -> "…produced by **lilting** prosody" as well as the
   literal case: `generate_forms("lilt")` already derives "lilting" as a rule-based
   `-ing` form, and a stored `Morphology.derivations` block (e.g. "annually" ->
   "annual") is unioned in the same way. Proper nouns are exempt (D-30, `Lexeme.kind`):
   "Larsen is a common Scandinavian surname" legitimately names its own entity. A
   multi-word headword ("ice axe") only counts when `find_span` matches the *whole*
   compound (its own separator-flexible multi-word matching, already built for exactly
   this) — a gloss that merely mentions "ice" or "axe" alone is not an offender, because
   no single-word candidate is ever added to the form list for a multi-word headword.

2. **One luna call per entry, all offending senses at once.** `CIRCULAR_GLOSS_INSTRUCTIONS`
   is bespoke prose (not sliced from `RENDITIONS_INSTRUCTIONS` the way the
   reading-level/register steps are, since this step only ever touches the one canonical
   target — no reading-level or register axis to restate) asking for a same-meaning
   definition that names neither the headword nor any form of it, plain register, length
   within about 30% of the original. `StageName.RENDITIONS` (luna): this is prose held to
   a meaning, not a structural verdict.

3. **Four free post-checks, mirroring `degenerate_renditions`' shape.** A rewrite is
   adopted only if all four pass, else the old text is kept and the reason logged
   (`content_hygiene_circular_rejected_<reason>`):
   - **`still_circular`** — `find_span` still places the headword or a form of it in the
     rewrite. Caught a real case in the pilot: `broadcaster:noun:1`'s rewrite kept using
     "broadcast" (a derived form of the headword).
   - **`headword_initial`** — `is_headword_initial` (D-30 exemption applies).
   - **`collision`** — the rewrite is identical (case/whitespace/trailing-period
     insensitive, `_normalised`) to a sibling gloss rendition, which would only relocate
     this defect into the one `degenerate_renditions` exists to catch.
   - **`drifted`** — the rewrite shares fewer than `_MIN_SHARED_CONTENT_WORDS` (2) content
     words (`hygiene.content_words`) with the gloss it replaces — this module's free
     proxy for "still means the same thing." Caught `silliness:noun:1` "An act or
     instance of silly behavior." rewritten to something sharing *no* content word with
     the original at all.

   Superseded text goes to a zero-cost `Provenance.note` (`CIRCULAR_GLOSS_NOTE =
   "superseded circular gloss: "`), and the D-47 marker
   (`content_hygiene:circular_gloss:<digest>;attempts=<n>`) bounds retries at two per
   entry, same as every other model step in this module.

4. **Runs before `degenerate_renditions`, after `fragment_examples`.**
   `ContentHygieneStep.ALL` gains `CIRCULAR_GLOSS` in that slot: `degenerate_renditions`
   compares a sense's renditions against its *canonical* gloss, so it must see the
   canonical text `circular_gloss` may just have rewritten, not the circular text that
   preceded it — running `circular_gloss` first means `degenerate_renditions` never
   spends a call comparing a sibling against a canonical that is about to change out from
   under it. Selectable on its own via `content-hygiene --only circular_gloss`.

5. **The sense's graded and register renditions are left untouched, on purpose.** They
   were generated independently from the old canonical text (`workflows/enrich.py`
   issues one call per `(owner, field)` covering every target rendition from the
   canonical it was told to hold to) and remain valid definitions of the same sense in
   their own right — a `grade_1` or `technical` rendition that happens not to repeat the
   headword is not wrong just because the canonical it was drafted from has since been
   reworded. Regenerating all nine non-canonical `(level, register)` targets at
   ~$0.0003/sense each to keep them "in sync" would spend roughly triple this whole
   pilot's cost on rewriting text that was never the defect. If a rendition *is* itself
   circular, `enrich.py`'s own generation-time check and `retrofit.py`'s
   `rendition_hygiene` pass (D-39) already cover it independently — this step's job is
   the canonical field alone.

**Measured on `data/sample-circular`** (300 fresh entries, seed 21, sampled uniformly
from every lexeme id in the production store — not a `tier2_50k.tsv` frequency window,
since circular glosses are not concentrated in any frequency band; copied read-only via
`scripts/build_sample_circular.py`, never written back).

Free detection (`scripts/circular_gloss_baseline_scan.py`), before any rewrite:

| | value |
|---|---|
| entries scanned | 300 |
| senses scanned | 826 |
| circular senses (full detector: headword + inflected/derived forms) | 181 (**21.9%**) |
| — of which, literal headword substring alone | 111 (13.4%) |
| — of which, only via an inflected/derived form | 70 (8.5%) |
| entries with >=1 circular sense | 128 / 300 (42.7%) |

The literal-only sub-rate (13.4%) lands almost exactly on the overnight scan's 13.0%
(2,040/15,718) — good agreement given the sample sizes, and evidence the overnight scan
was counting literal headword occurrences, not inflected/derived forms. This step's
broader detector (as designed, D-70 point 1) finds 63% more offenders than a literal-only
scan would, on this sample.

`content-hygiene --only circular_gloss --budget 1.00`, run to convergence (three sweeps —
the third made 0 calls, confirming the D-47 bound rather than an accident of scheduling):

| | value |
|---|---|
| calls (cumulative) | 135 (128 + 7 + 0) |
| rewritten (accepted, cumulative) | 179 |
| refused, final (2 attempts exhausted) | 2 — `rays:verb:0`, `shamed:adjective:0` |
| refused, by reason (attempt-events, cumulative) | `still_circular` 3, `collision` 3, `drifted` 3, `headword_initial` 0 |
| cost (cumulative) | $0.017554 |
| cost per rewrite | **$0.0000981** |
| cost per call | $0.0001300 |

179 of 181 (98.9%) offenders found were fixed within the two-attempt bound; the 2 that
were not (`rays:verb:0` "To emit or project rays or beams of light…", `shamed:adjective:0`
"Affective state of feeling shame or embarrassment…") keep their original text, carry a
marker recording two exhausted attempts, and cost nothing more on a third sweep — the
`test_the_attempt_bound_stops_at_two` behavior verified in isolation by unit test, here
confirmed against the real model on real refusals rather than a scripted one.

**Whole-store extrapolation** (labelled as such: both numbers below are estimates from
this one 300-entry pilot, not a measurement of the whole store). 110,869 live senses,
two ways to project the offender count:

- From the overnight scan's own rate (13.0%, the larger and more reliable sample):
  ~14,413 circular senses store-wide, ~10,222 entries (at this pilot's 1.41
  senses-per-offending-entry ratio) needing a call, **~$1.33** at this pilot's cost/call.
- From this pilot's own full-detector rate (21.9%, catching inflected/derived forms the
  overnight scan's literal-only count did not): ~24,291 circular senses, ~17,228 entries,
  **~$2.24**.

Both land comfortably under a $5 budget; a full-store run is a reasonable next step
rather than a further pilot.

**Before/after, read by hand (10 pairs):**

- `afire:adjective:0`: "Literal sense: afire denotes a state of burning or emitting
  flames, typically as the result of combustion." -> "Burning or emitting flames,
  typically as a result of combustion." — clean, meaning intact, shorter.
- `ampere:noun:1`: "The magnitude of electric current in a circuit, expressed in
  **amperes**, representing the rate of flow of electric charge." -> "The SI base unit of
  electric current, measuring the rate at which electric charge flows through a
  circuit." — better than the original: adds "SI base unit," which the circular original
  never actually said.
- `annually:adverb:0`: "…typically used when describing schedules, budgets, or **annual**
  events." (caught via the derived form "annual," not the headword itself) -> "Once each
  year, especially when referring to schedules, budgets, or recurring events." — correct
  catch, clean fix.
- `backhoe:noun:0`: "…combines a loader at the front with a rear **backhoe** excavating
  arm and bucket…" -> "…combining a front loader with a rear excavating arm and bucket,
  used for digging and moving earth." — meaning fully preserved.
- `clever:adjective:2`: "Of behavior or tactics: marked by guile or deception; using
  **clever** schemes or manipulation." -> "Marked by guile or deception; using ingenious
  schemes or manipulation." — good substitution, register held.
- `cosmological:adjective:0`: "Relating to **cosmology**, the scientific study of the
  origin, evolution, and large-scale structure of the universe." -> "Relating to the
  scientific study of the universe's origin, evolution, and large-scale structure." —
  clean.
- `diagnosed:verb:0`: "…evaluation of symptoms history and diagnostic tests resulting in
  a clinical **diagnosis**." -> "…evaluation of symptoms, history, and clinical tests." —
  fixed and, incidentally, better punctuated than the original.
- `gaze:noun:3`: "…the **gaze** denotes the act of looking as a social practice…" -> "…the
  act of looking is understood as a social practice…" — meaning fully preserved,
  passive-voice workaround is a little flat but not wrong.
- `vanilla:noun:0`: "The flavoring extract derived from the cured seed pods of the
  **vanilla** orchid…" -> "A flavoring extract derived from the cured seed pods of an
  orchid…" — correct fix; loses the specific orchid name, which is an acceptable trade
  for a one-sentence dictionary gloss.
- `wormwood:noun:0`: "The plant Artemisia absinthium, commonly known as **wormwood**, a
  perennial herb…" -> "A perennial herb of the Asteraceae family, native to Europe and
  western Asia, with bitter-tasting leaves and a history of use in medicine and
  flavoring." — the Latin binomial (`Artemisia absinthium`) survives; only the headword
  itself is gone.

All 10 read as legible, meaning-preserving improvements; none worse than the original.
The two free checks caught the two real failure modes actually seen in this sample: a
rewrite that swapped the headword for one of its own derived forms
(`broadcaster` -> "broadcast"), and one that paraphrased its way into a different
sentence rather than a rewritten one (`silliness:noun:1`, zero shared content words).

**Consequence.** `workflows/content_hygiene.py`: `ContentHygieneStep.CIRCULAR_GLOSS`,
`CIRCULAR_GLOSS_NOTE`, `_CIRCULAR_PREFIX`, `_MIN_SHARED_CONTENT_WORDS`,
`CIRCULAR_GLOSS_INSTRUCTIONS`, `_DraftCircularGlossRewrite(s)`, `_CircularGloss`,
`_circular_glosses`/`_build_circular_prompt`/`_circular_rewrite_is_usable`/
`_apply_circular_rewrite`/`_rewrite_circular`/`_circular_gloss_step`, wired into
`ContentHygieneStep.ALL` (before `degenerate_renditions`) and `_STEP_FUNCTIONS`; module
docstring gains a `circular_gloss` section and the step count moves 8 -> 9. `content
hygiene --only circular_gloss` already worked with no `cli.py` change, since that command
already accepts an arbitrary comma list of step names. Nothing here touches
`/home/mjbommar/projects/personal/opengloss-generator/data/core-store`; the pilot ran
only against this worktree's own `data/sample-circular`
(`scripts/build_sample_circular.py`, `scripts/circular_gloss_baseline_scan.py`,
`scripts/circular_gloss_pairs.py`). Tests: **+13** in `tests/test_content_hygiene.py` —
five pure detection tests (literal headword, inflected form via `generate_forms` alone
with no stored `Morphology`, proper-noun exemption, multi-word headword scoping both
ways), the accept path, one refusal test per free check (`still_circular`,
`headword_initial`, `collision`, `drifted`), zero-cost-when-clean, idempotence
(second sweep makes no calls), and step-selection in isolation; `tests/conftest.py`
gains one payload builder, `_circular_gloss_rewrite_payload`, and four marker headwords,
appended in the file's existing append-only block. `uv run ruff check`/`format`,
`uv run ty check`, `uv run pytest` are clean on `hygiene/circular-gloss`.

**Left undone.** The whole-store run itself: this decision pilots and ships the step,
but does not run it against `data/core-store`, which is outside this worktree's
permissions and is currently being read by the pair stages. The two extrapolations above
bound its cost comfortably under $5; a follow-up should run it for real and record the
actual count against this pilot's projection. Separately, the `drifted` check's floor
(2 shared content words) is a cheap proxy, not a meaning check — a rewrite that swaps two
content words for two synonyms and drops none would pass it while a human reviewer might
still flag drift; the pilot did not surface a case like that, but a larger run might.

## D-71 (2026-09-04) — The encyclopedia is entry-level; F1/F3/F4 were treating it as sense-level

**Context.** `Lexeme.encyclopedia` is one article per headword, written about the entry
as a whole — it has no owning sense. `export/triples.py` (F3, D-56), `export/qrels.py`
(F4, D-56), and `export/pairs.py` (F1, D-54) all read it as if it were, instead, a
positive belonging to *every* live sense of the entry: `export-triples` offered it as a
candidate positive text for any sense's queries; `export-qrels` never graded it at all
(a gap, not the described defect — see below); `export-pairs` paired it with every live
sense's representative example via `example_encyclopedia`.

The motivating case: `aaa` has six live senses (an auto-club sense, a credit-rating
sense, a battery-size sense, an IT-protocol sense, and two interjections) and one
encyclopedia article, about the string "AAA" as a cross-domain acronym. Before this fix,
`export-triples` over `data/core-store` paired `aaa:interjection:0#q1` — a synthetic
query for "a reflex cry from sudden pain" — with that article as its positive, and
`export-pairs` emitted the same pairing (encyclopedia article ↔ the interjection's own
example) as an `example_encyclopedia` positive. A retrieval or WiC model trained on
either row would learn that an interjection's query text should retrieve an article
about a telecom/finance/automotive acronym — a false positive with nothing to do with
word-sense disambiguation, just an artifact of the encyclopedia being entry-level.

**Verifying the qrels claim.** Before writing any patch, `export/qrels.py` was read in
full and traced against `tests/test_export_qrels.py`: neither the code nor the module
docstring nor D-56 itself ever mentions the encyclopedia. `build_qrels`'s `docs` dict is
populated only from `corpus.gloss[sense_id]`, and `GradedCandidate` never carried
anything but a sense id. Grading `aaa:encyclopedia` at 3 was never a real behavior on
`main` to regress — F4 simply never included the encyclopedia doc at all, a gap against
`docs/RETRIEVAL-DATA-PLAN.md`'s own F3+F4 section ("Positives: canonical gloss, one
example, encyclopedia neutral"), which named it for both features but was only ever
implemented for F3. Closing that gap with the same polysemy gate as F1/F3 (rather than
leaving F4 the one export silent on the encyclopedia) keeps all three exports
internally consistent, which is the property D-56 explicitly wants of the F3/F4 pair.

**Decision.** Gate every encyclopedia positive/grade on
`export.triples.is_monosemous(corpus, lexeme_id)` (exactly one live sense):

1. **`export-triples`.** `positive_options` no longer offers the encyclopedia entry as a
   candidate positive unless the sense's lexeme is monosemous. `Triple` gains
   `live_senses: int` (the query's own lexeme's live sense count), populated for every
   triple, so a downstream consumer can filter or reweight by polysemy without
   re-deriving it from `positive_id`.
2. **`export-qrels`.** New: a lexeme's encyclopedia doc (when it has one) is added as an
   extra graded candidate for every one of its senses' queries — `GRADE_OWN_SENSE` (3)
   when the lexeme is monosemous, `GRADE_ENCYCLOPEDIA_RELATED` (1; same numeric value as
   `GRADE_HYPERNYM_OR_COHYPONYM`, named separately since it is a different relationship
   that happens to earn the same relevance judgement) when it is polysemous, and never
   `GRADE_UNRELATED` (0) — same headword, entry-level, is never "unrelated." This is
   additive to the existing `MAX_GRADE_1`/`MAX_GRADE_2` caps, not subject to them (there
   is at most one encyclopedia doc per lexeme, so it never needs sampling).
   `GradedCandidate` is renamed `sense_id` → `doc_id` and gains a `text` field (populated
   at construction) so the encyclopedia doc — whose id/text do not come from
   `corpus.gloss` — does not need a second id-shaped lookup map.
3. **`export-pairs`.** `_encyclopedia_pairs` is skipped entirely unless the entry has
   exactly one live sense. `Pair` gains `live_senses: int` (how many live senses the
   entry named by `headword`/`sense_a` has), populated on every pair kind, including
   `wic_easy_negative` (which draws `headword`'s own live-sense count, not the partner
   headword's).

`is_monosemous`/`live_sense_count` live in `export/triples.py` (already the shared
projection module for F3/F4) and are imported by `export/qrels.py`, so F3/F4 can never
quietly disagree about which entries count as monosemous.

**Measured on a fresh 300-entry sample** (`aaa`, `people`, `bank`, `stubborn` explicit,
plus 296 random from `data/core/tier2_50k.tsv`, seed 0, copied read-only from
`data/core-store`; `--seed 0 --easy-negatives 1` for triples, `--seed 0` for qrels and
pairs):

| | before | after |
|---|---|---|
| `export-triples`: rows written | 16,044 | 16,044 (unchanged — only which text/id is the positive changes) |
| `export-triples`: rows whose positive is an encyclopedia doc | 4,848 (30.2%) | 336 (2.1%) |
| `export-qrels`: `docs.jsonl` size | 717 | 1,017 (+300 — every one of the 300 sampled entries has an encyclopedia article) |
| `export-qrels`: grade histogram | `{0: 25812, 1: 60, 2: 24, 3: 8604}` | `{0: 25812, 1: 7500, 2: 24, 3: 9768}` |
| `export-pairs`: `example_encyclopedia` rows | 717 (one per live sense) | 97 (one per monosemous entry) |

The qrels grade-3 delta (+1,164) is exactly the 97 monosemous entries × 12 queries/sense;
the grade-1 delta (+7,440) is exactly the 620 live senses belonging to the 203
polysemous-with-an-encyclopedia entries × 12 queries/sense. Confirmed directly:
`aaa:interjection:0#q1`'s triple positive changed from `aaa:encyclopedia` ("The string
AAA operates as a cross-domain acronym...") to `aaa:interjection:0#example`; its qrels
row for `aaa:encyclopedia` changed from absent to grade 1 (never 3, never 0); its
`example_encyclopedia` pair count for `aaa` changed from 6 (one per live sense) to 0.
`people`, `bank`, and `stubborn` are all polysemous with an encyclopedia article in the
sample and show the same shift. `data/core-store` was only ever read, never written, by
this verification.

**Consequence.** `export/triples.py` (`is_monosemous`, `live_sense_count`,
`Triple.live_senses`), `export/qrels.py` (`GRADE_ENCYCLOPEDIA_RELATED`,
`GradedCandidate.doc_id`/`.text`, `_encyclopedia_candidate`), `export/pairs.py`
(`Pair.live_senses`); `cli.py`'s `export-triples`/`export-qrels` docstrings;
`README.md`'s `export-pairs`/`export-triples`/`export-qrels` rows;
`docs/RETRIEVAL-DATA.md`'s F1 and F3+F4 sections (which also had a pre-existing broken
JSON code fence in the F3+F4 section, fixed in passing since it was mid-edit — its
closing content had been silently swallowed into the F5 heading). Tests: **+9**
(`tests/test_export_triples.py` — 3 new, 2 rewritten; `tests/test_export_pairs.py` — 2
new, 2 rewritten; `tests/test_export_qrels.py` — 4 new, 1 rewritten to exclude the
encyclopedia doc from a pre-existing `MAX_GRADE_1` cap check it does not participate in).
Counts drop: any downstream trainer or dataset snapshot built from a store with
polysemous encyclopedia entries will see fewer `example_encyclopedia` pairs and fewer
encyclopedia-sourced triple positives than before this fix — that is the fix working,
not a regression to chase.

## D-72 (2026-09-04) — The v2.0 release is a *family* of Hugging Face repos, two nested and thirteen flat

**Context.** `README.md`'s Status section listed "the HuggingFace export step" as the one
piece the v3 pipeline did not have. The question was not how to write parquet; it was
what shape the release should take. Two facts decided it.

First, **v1.3 shipped seven repos, and its flattest one was its most downloaded.** The
definition-level set — one row per sense definition, every column a scalar — outdrew the
nested dictionary set. That is the ordinary shape of dataset consumption: someone wants
*the definitions*, or *the examples*, or *the queries*, and wants to `load_dataset`,
`filter`, and train, not to explode a `list<struct>` first.

Second, **v2.0's content is genuinely nested.** A sense holds nine gloss renditions,
several example renditions, a relation list, twelve queries and seven QA pairs. Flatten
it once and you get a table with one row per gloss rendition; flatten it a different way
and you get one row per query. There is no single flat table that is not either lossy or
a cross-product.

**Decision.** Publish **fifteen** repos, registered once in
`src/opengloss_generator/export/hf_schemas.py`:

| | Repos |
|---|---|
| **Canonical, nested** | `lexicon` (one row per lexeme), `senses` (one row per live sense) |
| **Flat per-item views** | `definitions`, `examples`, `encyclopedia` (configs `encyclopedia` + `explanation`), `etymology`, `relations` (configs `relations` + `tombstoned`), `queries`, `qa-pairs`, `contrasts`, `provenance` |
| **Derived training sets** | `retrieval-pairs`, `retrieval-triples`, `qrels` (configs `listwise` + `docs`, plus `qrels.trec`), `pretrain` |

Every repo name is `opengloss-v2.0-<slug>`. **Every flat repo carries the join keys**
(`lexeme_id`, `sense_id`, `headword`, `pos`, `tier`) so it stands alone; the two nested
repos are the lossless view and the place a consumer goes when a flat one is not enough.

**Why the flat views are not redundant with the nested ones.** They are the same values
projected differently, and the exporter guarantees they agree by *construction*: the flat
row is built once and the nested `list<struct>` member is a key-subset of it
(`hf_rows._strip`), so a bug that gave the two different content would have to be a bug
in the projection helper, not a divergence between two builders. The cost is duplicated
bytes on the Hub, which is cheap; the benefit is that a consumer of `definitions` never
learns what a `Renditions[T]` is.

**Three judgement calls worth writing down.**

1. **Etymology is its own repo, not columns on the encyclopedia rows.** An etymology is
   one structured record per *entry* (a summary plus an ordered segment list); the
   encyclopedia table is *rendition*-grained, five rows per entry. Bolting the etymology
   onto it would repeat the whole structure once per reading level. The lexical
   explanation, by contrast, *is* rendition-grained, so it is a second config of
   `encyclopedia` rather than a repo of its own.
2. **Tombstoned relations are a config of `relations`, reconstructed from provenance.**
   D-65/D-68's reconcile steps remove edges from `Sense.relations` and write what they
   took out into the entry's provenance table, one line per edge. The exporter parses
   those records — using the prefixes imported from `relation_reconcile` itself, never a
   restated copy — into a `tombstoned` config with the type the edge carried *when it was
   removed* and the reason on it. The pre-demotion type is not invented, for D-65's
   reason: it was already gone before that pass ran.
3. **`tier` is a column on every row, and `unknown` is a real value.** Core and tier 2
   received every stage; tier 3 received the text stages only. Publishing that as a
   footnote would make the difference invisible to a `filter`. An entry on none of the
   three rank lists gets `tier = "unknown"` rather than a null, so a consumer filtering
   by tier never silently loses rows, and the coverage table in every card reports the
   share per field *per tier* rather than an average that hides the gap.

**Cards are generated, never written.** `export/hf_cards.py` renders each `README.md`
from f-string templates (no Jinja, no template files to keep in step with the package)
against the `Stats` the export just produced: every row count, coverage percentage,
histogram and example row in a card is counted from the rows that were actually written.
The fields table is rendered from the same `FieldSpec` tuples the `pyarrow` schema is
built from, so a column cannot exist in the parquet file and be missing from the card.
The prose that no export can compute — what the release is, how ids compose, what the
reading levels mean, what is wrong with it, the whole family table — lives once in that
module, so fifteen cards cannot disagree.

**Schemas are explicit.** Every column's `pyarrow` type is spelled out, nested columns
included; rows are projected onto the config's column list before writing and a row
carrying an unknown column raises rather than being silently dropped. Shards roll at
`--shard-rows` or `--max-shard-mb` (default 300 MB), whichever comes first, and a config
that wrote nothing still gets one empty, correctly-typed shard so a consumer's
`load_dataset` does not fail because a stage never ran.

**Uploading is a separate, explicit act.** `export-hf` is offline and free like every
other exporter. `--push` calls `HfApi.upload_large_folder` per repo (creating the repo
if absent, `--private` optional); `huggingface_hub` is an optional `hf` extra imported
only on that path, and the upload is unit-tested against a fake API rather than executed.

**Measured on `data/sample-300`** (300 entries, all core; 6.8 s, 10 MB across fifteen
repos): 300 lexemes, 1,041 live senses (168 retired senses skipped), 9,369 gloss
renditions, 6,655 examples, 17,111 relations (12,675 resolved), 2,784 queries, 6,289 QA
pairs, 5 contrasts, 14,258 provenance records ($2.90 of recorded generation), 24,590
retrieval pairs, 7,043 triples, 3,593 listwise queries over 1,341 docs, 3,600 pretraining
documents. The sample store predates the reconcile pass, so its `tombstoned` config is
empty — one shard, correct schema, zero rows, which is exactly the case the empty-shard
rule exists for.

**Consequence.** New: `export/hf_schemas.py` (the registry), `export/hf_rows.py` (the
projection and the statistics), `export/hf_cards.py` (the templates), `export/hf.py` (the
orchestration, sharding and push), `cli.py`'s `export-hf`, `tests/test_export_hf.py`
(+42). `pyproject.toml` gains `pyarrow` as a dependency, `huggingface-hub` as the `hf`
extra, and a `per-file-ignores` entry for the three card modules: their string literals
are *published prose*, so RUF001-003 would flag correct typography, and the SQL in a code
sample is shown to a reader rather than executed, so S608 does not apply.

## D-75 (2026-09-06) — v2.1: a sixteenth repo for inflections, tier 4 wired into the release, `--release` as a first-class parameter

**Context.** Two things landed independently of the exporter and both belong in the next
release. First, tier 4 (`data/core/tier4.tsv`) exists now — stopwords and high-frequency
compounds/names, folded into one `group` column distinguishing `stopword` from `wf10` —
and the tier-4 enrichment pass is running against the store as this is written. Second,
every card in D-72's family already carries `plural`, `past_tense` and the rest of
`Morphology` *inside* the nested `lexicon` row, but a consumer who wants to resolve one
surface string ("geese" -> "goose", noun) has to load the nested repo and explode it —
exactly the shape D-72 built the flat views to avoid.

**Decision, part 1: `inflections` is its own flat repo, not a config of `lexicon`.**
`opengloss-v2.1-inflections` gets one row per stored inflected or derived form, plus one
`lemma` row for the headword itself, per POS entry — so a homograph like "record" (noun,
verb) resolves the same headword to two different parts of speech rather than one
ambiguous row. Columns: `form` (case preserved), `form_normalized` (`form.lower()`, the
column a consumer filters on when the input's casing is unknown), `lexeme_id`,
`headword`, `tier`, `pos`, `relation` (`lemma`, one of the seven `Morphology` fields, or
`derivation`). It is a sibling repo rather than a config of `lexicon` for the same reason
D-72 gave etymology and relations their own repos: the grain is different (one row per
*form*, not per *lexeme*), and a repo carrying the join keys stands alone without forcing
a consumer through a `list<struct>` first. `hf_rows.RowBuilder._inflection_rows` reads
straight off each `POSEntry.morphology`, so it is a store-derived repo like the other
eleven, now twelve.

**Decision, part 2: tier 4 is a fourth value of the same `tier` column, not a new axis.**
`TIER_FILES` gains `(TIER_TIER4, "tier4.tsv")`; `TIERS` becomes `(core, tier2, tier3,
tier4, unknown)`. The source file's own `group` column (`stopword` vs. `wf10`) is *not*
surfaced as a fifth tier or a new column — both collapse into plain `tier4`, because the
release's tier granularity is "which frequency-ranked pass," not "which sub-population of
that pass." `TierIndex.from_dir` now logs a structured warning (`tier_file_missing`) for
any of the four files that is not on disk, rather than skipping it silently — the
tier-4 pass finishing after the exporter's next few runs is expected, not exceptional, and
a chain running unattended should say so in its own log rather than make a reader diff two
exports to notice a file went missing. `_read_tsv_words` already slugified the `word`
column before this change, so `"to be"` was already read as `to_be`; nothing there needed
to move, only confirming it in a test with tier 4's actual multi-word entries.

**Decision, part 3: nothing about tier count is hard-coded any more.** D-72's cards said
"three frequency-ranked passes" and named `tier3` specifically as the partial one, in
`hf_cards.py` prose and in `_lexeme_keys()`/`_sense_keys()`'s field descriptions. Both are
now computed: `_passes_note` counts and names `stats.tiers_present` (minus `unknown`)
for the "Coverage by tier" intro, and `_partial_tiers` derives which tiers are
"deliberately partial" from the coverage numbers themselves — a tier lacks full coverage
of queries, QA pairs or contrasts — rather than assuming tier 3 is the one that is short.
An export with two tiers, four tiers, or (once tier 5 or a tier-3b exists) five gets
correct prose without a further code change. `TIER_DESCRIPTIONS` gives the "By tier"
section its one required line each: core = top 10K by composite frequency; tier2 = ranks
to ~42K; tier3 = the rest of the frequency-ranked single words; tier4 = stopwords, plus
compounds and names at Wikipedia frequency >= 10.

**Decision, part 4: `--release` is a parameter everywhere a version used to be a
constant.** `hf_schemas.VERSION` (a module constant) becomes `DEFAULT_RELEASE = "v2.1"`
(a default argument): `RepoSpec.name`/`.repo_id`, `resolve_repos`, `hf.repo_dir`/
`data_dir`, `push_repos`, and `hf_cards.render_card` all take `release: str =
DEFAULT_RELEASE`. `export-hf` gains `--release`, so `--release v2.0` reproduces the old
family's naming exactly — same repo ids, same card headings, same cross-links — while the
default moves forward. The one place this could not be a plain function argument: a
repo's own `blurb`/`snippet` text is a module-level string constant naming *other* repos
in the family (`"...joined to opengloss-v2.0-senses..."`), fixed at import time, long
before any particular export's release is known. Those strings are now authored against
`PLACEHOLDER_RELEASE = "vX"` (`opengloss-vX-senses`) and `render_card` does one
`str.replace("opengloss-vX-", f"opengloss-{release}-")` pass over the fully assembled
card before returning it — the only substitution in the module, and it touches nothing
but this exact, deliberately-unreal token, so it cannot collide with genuine content.

**Measured on `data/sample-300`** (all 300 entries core; sixteen repos): the same counts
D-72 recorded, plus 3,023 inflection rows (one `lemma` row per POS entry plus every
non-empty `Morphology` field and recorded derivation) across those 300 lexemes.

**Consequence.** Changed: `export/hf_schemas.py` (`DEFAULT_RELEASE`,
`PLACEHOLDER_RELEASE`, `RepoSpec.name`/`.repo_id` take `release`, new `_INFLECTIONS`
`RepoSpec`, `_lexeme_keys()`/`_sense_keys()` tier descriptions generalized),
`export/hf_rows.py` (`TIER_TIER4`, `TIER_DESCRIPTIONS`, `TierIndex.from_dir` warns on a
missing file, `RowBuilder._inflection_rows`), `export/hf_cards.py` (`_passes_note`,
`_partial_tiers`, `_join_ticked`, `render_card`'s placeholder substitution, `release`
threaded through `_title`/`_family_table`/`_limitations`/`_loading_section`),
`export/hf.py` and `cli.py`'s `export-hf` (`--release`, threaded through), `tests/`
(+7: inflection rows including the lemma row and lower-casing, a missing-TSV warning, a
spaced multi-word tier-4 entry, card rendering with four tiers and with two, and
`--release v2.0` reproducing the old naming everywhere it appears).

## D-74 (2026-09-06) — `relation-regen`: filling the senses hygiene emptied out

**Context.** `relation-hygiene` (D-50) demotes an untrue edge to `see_also`;
`relation-reconcile` (D-65/D-68) tombstones it — takes it out of `Sense.relations`
entirely and writes what it removed to provenance. Both are correct, and both are silent
about what they leave behind: a sense every one of whose edges turned out to be wrong
ends the sweep with an empty relation list, and neither pass puts anything back. The
2026-09-05 store-wide audit counted 3,709 of 137,314 live senses in exactly that state
(`docs/CORE-DIARY.md`, "Goal 2 complete", open item 1), up from 1,760 the first time the
number was measured — every hygiene sweep since has grown it, and tier 4's thinner
multiword entries will grow it further. Nothing in the pipeline was aimed at it.

**Decision.** A new workflow, `workflows/relation_regen.py`, and CLI command
`relation-regen`. One luna call *per empty sense* (not per entry — pooling would buy
nothing when most affected entries have exactly one such sense), reusing
`StageName.SENSES`'s policy rather than adding a stage of its own — the same choice
`relation_hygiene`'s `validity` step made for `HYGIENE`, and for the same reason: this is
a generation task in the sense stage's own register, and a new stage would need its own
config policy, pricing coverage and cost-accounting plumbing that a pass this narrow does
not justify. Its contract and instructions are therefore module-private, following
`relation_hygiene` and `examples.py`'s sense-fit call, because `contracts.py` and
`prompts.py` are edited by other passes concurrently and a call that reuses an existing
stage has nothing to add to either file.

Each call is given the headword, the sense's part of speech and canonical gloss, one of
its own examples, the entry's *other* live senses' glosses (so the model does not hand
back a term that actually fits a sibling sense — the same discrimination context
`examples.py`'s sense-fit call gives), the domain, and — the property that matters most —
every target a hygiene pass already rejected *for this exact sense*, parsed from
`relation-reconcile`'s own tombstone provenance records
(`TOMBSTONE_RECORD_PREFIX`/`TOMBSTONE_LINE_PREFIX`, imported rather than restated), with
an instruction not to propose them again. Up to six typed relations come back — a strict
six-member subset of `RelationType` as its own model-facing enum, not the whole thing, so
`derivation`/`collocation`/`see_also`/etc. cannot appear in a response at all — each with
a one-line justification. Four free post-checks before anything is written: drop a target
equal to the headword, drop a target in the rejected set, drop an in-call duplicate, and
cap each type at `relation_reconcile.RelationCaps`' own allowance (synonym 8, antonym 4,
hypernym 3, hyponym 8) — the identical ceiling `relation-reconcile --only cap` enforces on
every other sense in the store. Relations are written **unresolved**
(`RelationTarget.sense_id` stays `None`) with note `regen: <justification>`; `resolve` and
`relation-hygiene` resolve and judge them on their next run. This pass does neither
itself — reimplementing either would be asking a generation call to grade its own answer,
the shape `docs/QA-DIARY.md` warns against elsewhere in this project.

Idempotence is D-47's shape with one adaptation: because the unit of work is one *sense*,
not one *entry* or one *ref set*, the marker is keyed on the sense id and digests the
sense's own canonical gloss text — `relation_regen:<sense id>:<gloss digest>;attempts=<n>`
— riding the call's own provenance record (`examples.py`'s convention: one call, one
marker, no separate zero-cost record). A sense a call actually filled is never revisited
at all, by construction — the selection rule is `not sense.relations`, read fresh every
sweep. A sense a call *failed* to fill (every proposal was the headword, already
rejected, a duplicate, or over its cap) keeps its marker until the gloss changes, and is
bounded at two attempts total even across gloss changes, so a stubborn sense cannot be
billed for a third opinion about input nothing has changed.

**Pilot** (`data/sample-relgen`, 300 entries copied read-only from the live
`data/core-store` — a tier-4 chain is running against it, so the copier
(`scripts/build_sample_relgen.py`, mirroring `build_sample_verdicts.py`'s pattern) only
ever reads, safe because `LexemeStore.write` swaps a complete file into place atomically —
selected by `audit.py`'s own `senses_zero_relations` rule: at least one live sense with an
empty relation list, stopping at 300 qualifying entries rather than scanning the whole
109K-entry store). Run at `--budget 1.00 --concurrency 8`; it did not need the cap:

| | |
|---|---|
| entries / senses scanned | 300 / 366 |
| calls | 366 (one per sense, as designed) |
| senses filled | 336 (91.8%); 30 still empty, marker written, one more attempt available |
| relations proposed → accepted | 680 → 678 |
| dropped: self / rejected / duplicate / capped / unusable | 0 / **1** / 0 / 1 / 0 |
| accepted by type | synonym 274, hypernym 249, antonym 62, hyponym 53, holonym 28, meronym 12 |
| cost | **$0.151276** total — **$0.00041/sense scanned**, $0.00045/sense filled |
| cache hit rate | 0.0% |

Extrapolated to the whole store's 3,709 known-empty senses: **≈ $1.53** — the pass is
cheap enough that the $10 default run budget covers several whole-store sweeps.

**The rejected-set exclusion fired, once, on exactly the shape it exists for.**
`ami:noun:1` ("the acronym for artificial moral intelligence...") carries a
`relation-reconcile` tombstone record naming `ethical AI`, `moral AI`, `responsible AI`,
`artificial intelligence` and `computational ethics` as already-demoted `see_also`
targets. The pilot's own call for that sense proposed two relations; one of them matched
an entry on that list and was dropped before it reached the sense (`dropped_rejected`),
the other (`hyponym -> machine ethics`) was kept. Out of 680 proposals across the whole
pilot, exactly one repeated a tombstoned target — the model does not reach for rejected
terms often, but the guard is what caught the one time it did, on the single most obvious
candidate a term like "AMI" invites. The per-type cap fired once too: `puma:noun:1` (the
footwear company sense) proposed four hypernyms (`corporation`, `enterprise`,
`multinational corporation`, and a fourth); the fourth was dropped and the sense kept
exactly three, `relation_reconcile.RelationCaps`' own hypernym ceiling.

**The read.** Fifteen filled senses sampled at random (`random.seed(7)` over the full
336) and read by hand. Thirteen were correctly typed with a justification that actually
supports the claim — surname → `name`/`anthroponym`/`forename` hypernyms, `annealing` →
`metaheuristic`/`stochastic algorithm`/`optimization`, `basically` → `more or
less`/`kind of`/`sort of`, `beleaguered` → `lay siege` (synonym) / `surround` (hypernym).
Two were borderline rather than wrong, and both are exactly the shape the *next* sweep
exists to catch, which is the point of not judging this pass's own output here:
`repellant` → `repellent` (`synonym`) is arguably a spelling variant of the same word
rather than a second lexical unit — the same defect class `relation_hygiene`'s inflection
step targets, just not a *headword* inflection so it slipped this pass's own headword-only
self-check; and `beleaguered` → `siege` (`holonym`, "the act of laying siege forms part of
... a siege") is a near-tautological justification a validity verdict should reasonably
question. Both are single, resolvable, unresolved edges sitting exactly where `resolve`
and `relation-hygiene` will look next — not a reason to add a fifth post-check here.

**Consequence.** New: `workflows/relation_regen.py` (workflow + module-private contract
and instructions), `cli.py`'s `relation-regen` command and
`_relation_regen_dry_run_estimate` (dry-run pricing now measured, not modelled, from this
pilot), `scripts/build_sample_relgen.py`, `tests/test_relation_regen.py` (+16: rejected-
set parsing and exclusion, self/duplicate/cap post-checks, marker idempotence and the
two-attempt bound across gloss changes, the strict six-member enum, CLI dry-run pricing
and `--from-list`), one payload builder appended to `tests/conftest.py`
(`_relation_regen_payload`). `uv run ruff check`/`format`, `uv run ty check`,
`uv run pytest` (1,188 passed, 2 pre-existing skips) are clean on
`hygiene/relation-regen`.

**Left undone.** The whole-store run itself: this decision pilots and ships the pass, but
does not run it against `data/core-store`, which a tier-4 chain is currently writing to
and which is outside this worktree's permissions. The pilot's extrapolation bounds it
comfortably under $2; a follow-up should run it for real, then `resolve` and
`relation-hygiene` again to close the loop this pass deliberately leaves open, and record
how many of the 3,709 (now more, tier 4 included) end up genuinely unfillable after two
attempts each.

## D-73 (2026-09-06) — `relation-reconcile --only retype`: buying back the direction D-68 threw away

**Context.** D-68 made `verdicts` demote every `related_differently` contrast edge to
`see_also`, argued at length that this was the honest disposal, and then wrote down what it
cost. Of fifteen such verdicts read against the paragraphs they came from, **eleven were a
synonym that is really a hypernym or a hyponym** — `falcon→peregrine`, `dictator→pharaoh`,
`accusation→indictment`, `psychic→clairvoyant`, `operative→shinobi` — and *in every one the
paragraph names which member is the broader term, in prose*. That decision's own words: "the
information to retype these correctly exists, in the contrast text, and reading it back out
would need a model call this pass does not make… the obvious next feature, and it is not
this one." This is that feature.

**Decision.** A sixth step in `relation_reconcile.py`, `retype`, **first in the step order**,
and the only one in the module that spends — which is why `relation-reconcile` now takes
`--budget`.

1. **One strict-enum question per edge.** For each `related_differently` contrast still keyed
   on a live `synonym` or `antonym` edge, one nano call (`StageName.HYGIENE`'s existing
   policy — a structural verdict about two definitions, exactly what `relation_hygiene`'s
   `validity` step uses it for; no new stage member, no new price row) is shown the two
   headwords, both glosses, the type on file and the paragraph, and answers one of
   `hypernym | hyponym | co_hyponym | antonym | synonym | none`. `confusable_with` is the
   third symmetric type and is deliberately excluded: its claim is about spelling and misuse,
   its schema requires a note that *is* the content, and "which of these two is broader" is
   not a question its contrasts were written to answer.

2. **Direction is the whole problem, so the prompt is built around it.** This project's
   convention is that a relation type names what the **target** is to the source
   (`relation_hygiene.RELATION_VALIDITY_INSTRUCTIONS`: "this sense IS A KIND OF the target"),
   which is the opposite of the convention a reader is likelier to have met, where "X is a
   hyponym of Y" describes X. The instructions therefore define each type by the sentence
   that has to read as true with SOURCE and TARGET named in it, work the *same* pair in both
   directions, and — after the pilot below found the failure mode — name the failure mode
   outright: "the one way this question is commonly got wrong is answering with the word that
   describes the SOURCE."

3. **Four dispositions.** A type other than the one on file **retypes** the edge, note
   `retyped: contrast <old>→<new>`. The type already on file **keeps** it, note
   `kept: contrast <type> confirmed` — a fresh, focused judgement about this one pair is
   better evidence than the by-product verdict it disagrees with, and a contrast may
   therefore disagree with its own verdict. `co_hyponym` and `none` change nothing and leave
   the edge for `verdicts` to demote exactly as D-68 does. `co_hyponym` earns its place in
   the enum without a `RelationType` behind it: a co-ordinate pair under one parent is a real
   and common answer, and offering it is what stops the model rounding a sibling pair up to
   `synonym` or down to `none`.

4. **The hand-off to `verdicts`.** `retype` runs first and `_apply_verdicts` skips any edge
   carrying one of `RETYPE_NOTE_PREFIXES`. The note is load-bearing for exactly one of the
   four outcomes: a *retyped* edge no longer matches its contrast's edge id and would be
   skipped anyway, but a *kept* one still matches, and demoting it would undo, a few lines
   later and in the same sweep, the only judgement anybody ever bought about that pair.
   Neither note is a demotion prefix, so `tombstone` leaves both alone and
   `graph_hygiene._asserted_pairs` does not read them as "a pass judged this pair away".

5. **Far side: the inverse, not a second call.** A contrast is written once per *undirected*
   pair (D-57 §1), so the reverse edge has no contrast and nothing else will ever reach it —
   and leaving it is strictly worse than doing nothing, since `A --hypernym--> B` beside
   `B --synonym--> A` contradicts itself where before it was merely wrong the same way at
   both ends. A retype queues a `_FarSideRetype`; the second phase gives the reverse
   `hypernym↔hyponym` swapped, `synonym`/`antonym` unchanged. The far-side identity test is
   **sense-level** (D-68 §3's argument, copied) and accepts two shapes: the reverse still
   carrying the near side's old type, and a `see_also` a previous sweep demoted — which is
   the D-68 leftover this step exists to rescue. No second model call: the inverse of a
   decided direction is arithmetic, not judgement. Run after the main sweep has fully
   drained so no two entry locks are ever held at once (D-31), and **with no stop event**,
   for the reason and by the mechanism D-50's second amendment gives.

6. **D-47 marker on the contrast set, two attempts.** `relation_reconcile:retype:<digest>;attempts=<n>`,
   digest over one `<edge id>=<verdict>` ref per candidate **as collected**, not as the
   answers leave it — the opposite of `relation_hygiene`'s `validity` marker and the right way
   round here, because `co_hyponym` and `none` leave their edge untouched and still matching
   its contrast, so a digest over survivors would be a digest over exactly the questions
   already paid for. Written whenever a call was answered, even when nothing moved: the
   marker is what the calls bought.

7. **A default selection is every step the caller is equipped for.** `only=None` with a
   runner is all six; without one it is the five free steps, so every library caller that has
   always run this pass for $0 still does. Naming `retype` with no runner raises — a caller
   who asked for the step by name and silently got a free sweep that changed nothing is owed
   an error.

8. **`--dry-run` stays free and becomes a plan.** The five free steps compute every edit and
   write nothing, as before; `retype` counts the calls it would have made into
   `calls_planned` and makes none, and the CLI prices them off the `HYGIENE` policy's row.
   The summary says so, because in a dry run the *other* steps' counts are the counts for a
   sweep in which `retype` changed nothing.

**The population, and why the pilot had to rewind.** The step acts on a `related_differently`
contrast whose edge is still live and still symmetric. On `data/core-store` today that
population is **empty**: a full scan of all 109,633 entries found 24,611 `related_differently`
contrasts and **zero** still matching a live edge, because D-68's `verdicts` demoted them and
`tombstone` removed them weeks before this step existed. The step is therefore forward-looking
— it earns its keep on entries whose contrasts are newer than their last reconcile, which is
every entry the `contrasts` stage reaches from here on — and it does nothing at all to an
entry already past that point. `scripts/build_sample_retype.py` rebuilds the pre-`verdicts`
state from evidence rather than guesswork: a contrast's key *is* the edge it was written
about (`falcon:noun:0-synonym->peregrine` names the asserting sense, the type and the target
lexeme), so where that edge is gone the script re-adds a relation of that type toward that
target, taking the target's sense from `Contrast.target_sense_id` and its surface form from
the target entry's own headword, so no slug is ever un-slugged. The reverse is restored the
same way where the far entry no longer holds one. `data/core-store` is read-only throughout;
the sample is a copy.

**Measured (`data/sample-retype`, 400 contrast-bearing seeds from `core_10k.tsv` and
`tier2_50k.tsv` plus 18,749 far sides, `--from-list` the seeds, `--concurrency 8`,
`--budget 1.00`).**

| | |
| --- | --- |
| Edges judged | **962** on 400 entries (962 restored contrast edges, 743 restored reverses) |
| Calls · cost · duration | 962 · **$0.180192** · 178 s — **$0.000187 per call** |
| Tokens per call | 1,390 input, 0 cached, 77 output (cache hit rate 0.0 across all 962) |
| Answers | `hyponym` 396 · `none` 350 · `hypernym` 102 · `co_hyponym` 84 · `antonym` 26 · `synonym` 4 |
| Near-side retypes | **500** — `synonym→hyponym` 395, `synonym→hypernym` 102, `antonym→hyponym` 2, `synonym→antonym` 1 |
| Near-side kept | **29** — `antonym` confirmed 25, `synonym` confirmed 4 |
| Far-side inverse retypes | **380**, of 500 queued (the other 120 far entries hold no reverse of that type resolved to that sense) |
| Left for `verdicts` | **433** = the 350 `none` + 84 `co_hyponym`, minus one edge a duplicate had already taken; `verdicts` then demoted 433 near and 341 far |
| Rescued from demotion | **529 of 962 (55.0%)** — edges D-68 alone would have demoted to `see_also` and tombstoned |
| Second sweep | 381 of 400 skipped by digest, 12 calls ($0.0022), 1 retype; **third sweep 0 calls** |

The 5:1 skew toward `hyponym` is a property of the sample, not of the model: seeds are drawn
in rank order from the core word lists, the frequent word of a pair is usually the general one
(*falcon* before *peregrine*), so the source is systematically the broader member. The model
still answered `hypernym` 102 times, which is what says it is reading rather than defaulting.

The 12 calls on the second sweep are the honest cost of the far-side phase writing no marker
(D-68's rule, kept): a seed that is also the far side of another seed has one candidate
retyped out from under its retype marker, so its candidate set changes and D-47 grants
attempt 2. It is bounded at two attempts and converges on the third sweep, at 1.2% of the
first sweep's bill.

**The 20-edge read, and the prompt fix it forced.** The pilot was run twice, and the first run
is why. In run A (962 calls, $0.154) a seeded-random 20 near-side retypes plus a spot check of
5 of the 82 `hypernym` answers found **three inverted directions, all the same failure**: the
model was answering with the word that describes the **source**. `die --synonym--> drown` came
back `hypernym` (*die* is indeed the hypernym — but the answer names the target, and *drown* is
narrower); `agent --synonym--> depute` the same; `agency --synonym--> firm` came back `hyponym`
where the paragraph says in so many words "agency is a kind of firm". The instructions were
rewritten to name that failure mode outright, to lead each definition with what the TARGET is,
and to carry `die`/`drown`, `agency`/`firm` and a part-of-speech `none` case as worked
examples. Run B is the measurement above. All four of run A's misjudged edges are now right:
`agency→firm` `hypernym`, `die→drown` `hyponym`, `agent→depute` `hyponym`, `moving→travel` no
retype at all.

Twenty run-B retypes read against their paragraphs — fourteen seeded-random plus six drawn
from the `hypernym` answers, deliberately over-weighting the minority class where the errors
live. **Sixteen are right, two are the wrong type without being the wrong direction, and two
are inverted.**

*Right (16).* `state:noun:1→texas` and `country:noun:0→montenegro` (hyponym — proper names of
one political unit; `instance_of` would be better still and is not in the enum, which is worth
a later look), `machine:verb:0→operate` (hypernym: "operate is the ordinary and more flexible
choice"), `strong:adjective:2→unbreakable` (hyponym: "unbreakable makes a much more absolute
claim"), `date:noun:0→timestamp`, `address:noun:1→plenary` ("an address can be plenary"),
`body:noun:1→bulk` ("bulk is therefore a narrower physical-size term"),
`count:verb:0→quantify` and `agent:noun:0→doer` and `defense:noun:1→vindication` and
`sound:verb:1→travel` and `crime:noun:0→misdeeds` and `sound:noun:0→vibration` and
`local:adjective:0→regional` (hypernym, each one following the paragraph's own "X is the
broader word"), `quality:noun:1→savor` ("savor names a particular kind of quality"),
`business:noun:0→trades` ("business is the broader").

*Wrong type, right direction (2).* `near:adverb:0→close` was retyped `hypernym` where the
paragraph argues the two adverbs are interchangeable — `synonym`, i.e. a *keep*, was the
answer. `light:noun:2→photon` was retyped `hyponym` where the paragraph says outright "the
relation is whole-to-unit, not synonymy" — a `meronym`, which the enum does not offer, so
`none` was the honest answer.

*Inverted (2).* `degree:noun:3→unit` came back `hyponym` where the paragraph says "a degree is
one particular kind of unit" — the target is the broader word and the answer should have been
`hypernym`. `film:noun:0→skim` came back `hypernym` where the paragraph says "film is the
wider term" — the answer should have been `hyponym`. The two err in *opposite* directions, so
what is left after the prompt fix is noise rather than the systematic source/target confusion
run A had.

**The judgement.** Ship it, with the error rate written down. Two inversions in twenty — on a
sample deliberately enriched with the hard class, so ~10% is an upper bound rather than the
sweep's rate — against the alternative of deleting all 962 edges, which is what D-68 does and
which loses 100% of the information rather than 10%. An inverted retype is worse than a
deletion in one specific way and better in every other: the far-side inverse makes the pair
*consistent*, so `graph_hygiene`'s mutual-hypernymy and cycle detectors will not catch it, but
every one of these edges carries `retyped: contrast <old>→<new>` on its note and is therefore
findable, countable and reversible as a class, which a tombstoned edge is not. If 10% is later
judged too high, the lever is a stage policy of its own at a higher `reasoning_effort` — the
`HYGIENE` policy is `"low"` and is shared with `relation_hygiene`, so raising it here means a
new `StageName` member and price row, which is a bigger change than this step needed.

**Consequence.** Modified: `src/opengloss_generator/workflows/relation_reconcile.py`
(`RelationReconcileStep.RETYPE` first in `ALL`; `CONTRAST_RETYPE_INSTRUCTIONS`,
`_DraftContrastRetype`, `_RetypeCandidate`, `_collect_retype_candidates`, `_apply_retypes`,
`_apply_retype`, `_build_retype_prompt`, `_target_gloss`, the retype marker helpers,
`_FarSideRetype`/`_far_side_retype`/`_is_far_side_retype_of`/`_retype_far_side`/
`_retype_far_side_all`; `RETYPE_NOTE_PREFIX`, `RETYPE_KEPT_NOTE_PREFIX`,
`RETYPE_FAR_SIDE_NOTE_PREFIX`, `RETYPE_NOTE_PREFIXES`, `RETYPE_MARKER_PREFIX`,
`RETYPE_MAX_ATTEMPTS`; `by_answer`/`by_retype`/`calls_planned`/`far_side_retyped` on
`RelationReconcileStepResult` and `retyped`/`calls`/`cost_usd` on the outcome; `_select_steps`
split out of the entry point; the `runner` parameter and the ``retype``-without-a-runner
refusal); `cli.py` (`--budget` on `relation-reconcile`, `session.stages` passed through,
`_retype_plan` and its three measured token constants, the `--only` help and the command
docstring); `README.md` (the `relation-reconcile` row). New:
`scripts/build_sample_retype.py`. Tests: **+18** (`tests/test_relation_reconcile.py`: each of
the six answers' disposition, the far-side inverse and its `see_also` rescue, the two
directions of one pair coming back inverse, a kept edge surviving `verdicts` in the same
sweep, a retyped edge surviving `verdicts` and `tombstone`, idempotence over three sweeps, the
non-`related_differently` verdicts never asked about, asymmetric edges never asked about, the
dry-run plan, the runner refusal, and a default selection without a runner staying free), plus
one scripted contract in `tests/conftest.py`; 1,188 pass, 2 pre-existing skips.
`uv run ruff check`/`format --check`, `uv run ty check src`, `uv run pytest` clean on
`hygiene/retype`. `data/core-store` untouched — the sample is a read-only copy.
