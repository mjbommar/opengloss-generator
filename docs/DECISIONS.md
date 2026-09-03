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
