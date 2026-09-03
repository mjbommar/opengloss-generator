# External standards crosswalk — a citable taxonomy survey

Research date: 2026-09-02. Every value list below was fetched from a live source where the
source permitted automated access; where a fetch was blocked (403, JS-rendered shell, paywall),
that is stated explicitly and the fallback source is named, following the same discipline
`docs/REGISTERS.md` uses. Scope: this note evaluates ten fields/enums in
`src/opengloss_generator/schema.py` and `src/opengloss_generator/taxonomy.py` against the
external standard(s) each most plausibly should interoperate with, and recommends, per field,
whether to **adopt** the standard's values as our stored values, **store a mapping** only (keep
our values, add a crosswalk table used at export/interop time), or **leave as is** with no
change at all. **No source code was changed as part of this research.**

The short version, elaborated in § 11: none of the ten fields warrant replacing their stored
values outright. Two (`frequency`, `EtymologySegment.language`) have a genuine gap — a missing
corpus-size field and unconstrained free text, respectively — that should be closed. One
(`Assessment.qa_flags`) should move from free text to a closed, standards-grounded enum. The
rest are correctly scoped narrower or differently than their nearest standard and should stay
as is, with an export-time crosswalk table recorded here as the citable reference.

---

## 1. PartOfSpeech → Universal Dependencies UPOS v2 / LexInfo 3.0

Our enum (`src/opengloss_generator/schema.py`, `PartOfSpeech`): `noun, verb, adjective, adverb,
pronoun, preposition, conjunction, determiner, interjection, numeral`.

### 1a. UD UPOS v2 — verbatim (fetched live from <https://universaldependencies.org/u/pos/>)

**Open class words**

| Tag | Gloss |
|---|---|
| `ADJ` | adjective — words that modify or describe nouns |
| `ADV` | adverb — words that modify verbs, adjectives, or other adverbs |
| `NOUN` | noun — words representing persons, places, things, or concepts |
| `PROPN` | proper noun — specific names of individuals, places, or organizations |
| `VERB` | verb — words expressing actions, states, or occurrences |

**Closed class words**

| Tag | Gloss |
|---|---|
| `ADP` | adposition — words indicating relationships between other words (prepositions/postpositions) |
| `AUX` | auxiliary — verbs that modify or support main verbs |
| `CCONJ` | coordinating conjunction |
| `DET` | determiner |
| `NUM` | numeral |
| `PART` | particle — grammatical function, no clear POS category |
| `PRON` | pronoun |
| `SCONJ` | subordinating conjunction |

**Other**

| Tag | Gloss |
|---|---|
| `INTJ` | interjection |
| `PUNCT` | punctuation |
| `SYM` | symbol |
| `X` | other — doesn't fit standard categories |

17 tags total. Source: <https://universaldependencies.org/u/pos/>.

### 1b. LexInfo 3.0 PartOfSpeech individuals

LexInfo's `PartOfSpeech` class has no closed OWL enumeration — it's an open set of named
individuals in a large OWL/RDF file, so this is the *verified subset*, not a complete list.
Cross-checked against two independent sources — a direct fetch of the ontology
(`lexinfo.net/ontology/3.0/lexinfo`) and PreMOn's generated vocabulary reference
(<https://premon.fbk.eu/apidocs/eu/fbk/dkm/premon/vocab/LEXINFO.html>, which mechanically lists
`lexinfo:*` individuals typed `lexinfo:PartOfSpeech`) — the confirmed union is:

```
lexinfo:noun, lexinfo:verb, lexinfo:adjective, lexinfo:adverb, lexinfo:pronoun,
lexinfo:determiner, lexinfo:article, lexinfo:adposition, lexinfo:preposition,
lexinfo:conjunction, lexinfo:coordinatingConjunction, lexinfo:subordinatingConjunction,
lexinfo:numeral, lexinfo:cardinalNumeral, lexinfo:particle, lexinfo:interjection
```

Caveat: `lexinfo:properNoun`/`lexinfo:commonNoun` were **not** independently confirmed as
distinct individuals (unlike UPOS's `PROPN`) in two fetch attempts — treat their absence here as
"not found," not "confirmed absent." LexInfo is a deliberately open, superset vocabulary; this is
the verified subset relevant to our enum, not the complete list.

### 1c. Crosswalk

| Ours | UPOS | LexInfo | Notes |
|---|---|---|---|
| `noun` | `NOUN` | `noun` | clean 1:1 |
| `verb` | `VERB` | `verb` | clean 1:1 (UPOS separates `AUX`; we have no analogue — lossy if `AUX` is ever adopted) |
| `adjective` | `ADJ` | `adjective` | clean 1:1 |
| `adverb` | `ADV` | `adverb` | clean 1:1 |
| `pronoun` | `PRON` | `pronoun` | clean 1:1 |
| `preposition` | `ADP` | `preposition` (subtype of `adposition`) | **lossy**: UPOS's `ADP` also covers postpositions; ours doesn't distinguish adposition subtype |
| `conjunction` | `CCONJ` **or** `SCONJ` | `conjunction` (with `coordinatingConjunction`/`subordinatingConjunction` subtypes) | **one-to-many**: our single value is ambiguous between UD's two conjunction tags |
| `determiner` | `DET` | `determiner`/`article` | **one-to-many** on the LexInfo side (article split out); UPOS is clean 1:1 |
| `interjection` | `INTJ` | `interjection` | clean 1:1 |
| `numeral` | `NUM` | `numeral`/`cardinalNumeral` | UPOS clean; LexInfo one-to-many |
| *(none)* | `PROPN` | *(unverified)* | we fold proper nouns into `noun` + `LexemeKind.PROPER_NOUN`/`ProperNounInfo` — a deliberate design choice, not a gap |
| *(none)* | `AUX` | *(unverified)* | no auxiliary-verb tag; auxiliaries are tagged `verb` |
| *(none)* | `PART` | `particle` | no analogue in ours |
| *(none)* | `SYM`, `PUNCT`, `X` | *(n/a)* | not applicable to a dictionary headword's POS |

### 1d. Recommendation: store a mapping only; leave the stored enum as-is

`PartOfSpeech` tags dictionary headword senses (`POSEntry.pos`), not tokens in running text. UD's
tagset is built for treebank annotation of individual word tokens, where a determiner-vs-article
or coordinating-vs-subordinating distinction is locally recoverable from surrounding context. A
headword like "for" is both a preposition and a conjunction depending on sense, and our schema
already handles that correctly via separate `POSEntry` entries per POS — adopting UPOS's
`CCONJ`/`SCONJ` split would just push the same ambiguity into the enum without adding
information, since a dictionary sense carries no syntactic context to disambiguate from. Store
the crosswalk above for interoperability (e.g. emitting UPOS tags into a CoNLL-U-adjacent
export), but keep `PartOfSpeech` in `schema.py` unchanged.

### 1e. Migration note

No schema migration needed — this is additive documentation only. If UD interop is needed later,
`conjunction` requires a manual/heuristic split at export time (default to `CCONJ` unless the
sense's gloss/examples indicate subordination), and `determiner` needs a secondary "is this an
article" flag if LexInfo's finer split is wanted.

---

## 2. RelationType → Global WordNet Association relation inventory / SKOS

Our enum (`src/opengloss_generator/schema.py`, `RelationType`): `synonym, antonym, hypernym,
hyponym, meronym, holonym, derivation, collocation, confusable_with, see_also, causes, entails,
used_with, instance_of`.

### 2a. GWA / WN-LMF `relType` enumerations — verbatim (from the WN-LMF DTD linked at <https://globalwordnet.github.io/schemas/>, `WN-LMF-1.4.dtd`)

**`SynsetRelation` `relType`** (concept-to-concept):

```
agent | also | attribute | be_in_state | causes | classified_by | classifies |
co_agent_instrument | co_agent_patient | co_agent_result | co_instrument_agent |
co_instrument_patient | co_instrument_result | co_patient_agent | co_patient_instrument |
co_result_agent | co_result_instrument | co_role | direction | domain_region |
domain_topic | exemplifies | entails | eq_synonym | has_domain_region |
has_domain_topic | is_exemplified_by | holo_location | holo_member | holo_part |
holo_portion | holo_substance | holonym | hypernym | hyponym | in_manner |
instance_hypernym | instance_hyponym | instrument | involved | involved_agent |
involved_direction | involved_instrument | involved_location | involved_patient |
involved_result | involved_source_direction | involved_target_direction |
is_caused_by | is_entailed_by | location | manner_of | mero_location |
mero_member | mero_part | mero_portion | mero_substance | meronym | similar |
other | patient | restricted_by | restricts | result | role | source_direction |
state_of | target_direction | subevent | is_subevent_of | antonym | feminine |
has_feminine | masculine | has_masculine | young | has_young | diminutive |
has_diminutive | augmentative | has_augmentative | anto_gradable | anto_simple |
anto_converse | ir_synonym
```

**`SenseRelation` `relType`** (word-sense-to-word-sense):

```
antonym | also | participle | pertainym | derivation | domain_topic |
has_domain_topic | domain_region | has_domain_region | exemplifies |
is_exemplified_by | similar | other | simple_aspect_ip | secondary_aspect_ip |
simple_aspect_pi | secondary_aspect_pi | feminine | has_feminine | masculine |
has_masculine | young | has_young | diminutive | has_diminutive | augmentative |
has_augmentative | anto_gradable | anto_simple | anto_converse | metaphor |
has_metaphor | metonym | has_metonym | agent | material | event | instrument |
location | by_means_of | undergoer | property | result | state | uses |
destination | body_part | vehicle
```

### 2b. SKOS semantic relations & mapping properties — from <https://www.w3.org/TR/skos-reference/> (§8, §10)

| Property | Definition |
|---|---|
| `skos:broader` | `<A> skos:broader <B>` asserts `<B>` is a broader concept than `<A>`. Sub-property of `skos:broaderTransitive`. |
| `skos:narrower` | `owl:inverseOf skos:broader`. Sub-property of `skos:narrowerTransitive`. |
| `skos:related` | Sub-property of `skos:semanticRelation`; asserts an *associative* (non-hierarchical) link; `owl:SymmetricProperty`; disjoint with `skos:broaderTransitive`. |
| `skos:broaderTransitive` / `skos:narrowerTransitive` | `owl:TransitiveProperty` pair, inverses of each other. |
| `skos:exactMatch` | Sub-property of `skos:closeMatch`; high-confidence interchangeability across vocabularies; `owl:SymmetricProperty` and `owl:TransitiveProperty`. |
| `skos:closeMatch` | Sub-property of `skos:mappingRelation`; "sufficiently similar" for interchangeable use in some IR applications; `owl:SymmetricProperty`. |
| `skos:broadMatch` / `skos:narrowMatch` | Sub-properties of `skos:mappingRelation` and of `skos:broader`/`skos:narrower`; cross-vocabulary hierarchical mapping. |
| `skos:relatedMatch` | Sub-property of `skos:mappingRelation` and of `skos:related`; `owl:SymmetricProperty`; cross-vocabulary associative mapping. |

### 2c. Crosswalk

| Ours | GWA/WN-LMF status | SKOS interop |
|---|---|---|
| `hypernym` | **standard**, exact match | ≈ `skos:broader` (loosely) |
| `hyponym` | **standard**, exact match | ≈ `skos:narrower` |
| `synonym` | **standard**, but WN-LMF names it `eq_synonym` (cross-resource) or `similar` (intra-lingual near-synonymy) — no bare `synonym` value exists since within-synset terms are synonymous by construction | ≈ `skos:closeMatch`/`skos:exactMatch` for cross-vocabulary alignment |
| `antonym` | **standard**, exact match (both relation lists) | no clean SKOS analogue |
| `meronym` | **maps to a finer standard set**: `mero_part`, `mero_member`, `mero_substance`, plus `mero_location`, `mero_portion`, and a catch-all `meronym` for the unspecified case | n/a |
| `holonym` | **maps to a finer standard set**, symmetric to meronym: `holo_part`, `holo_member`, `holo_substance`, `holo_location`, `holo_portion`, plus catch-all `holonym` | n/a |
| `derivation` | **standard**, exact match (`SenseRelation`) | n/a |
| `causes` | **standard**, exact match (inverse `is_caused_by`) | n/a |
| `entails` | **standard**, exact match (inverse `is_entailed_by`) | n/a |
| `instance_of` | **standard**, but named `instance_hypernym` (inverse `instance_hyponym`) — same concept, different label | n/a |
| `see_also` | **standard**, matches `also` in both relation lists | ≈ `skos:related` (loosely) |
| `confusable_with` | **no standard equivalent** — GWA has no "commonly confused" relation | none |
| `used_with` | **no standard equivalent** — closest concepts (`co_role`, `involved_*`) are argument-structure relations between predicate senses, not surface collocation | none |
| `collocation` | **no standard equivalent** — out of scope for a synset/sense-relation model | none |

### 2d. Recommendation: adopt standard names where clean, mapping-only where finer, `og:`-namespace the rest

Adopt the standard names as stored values where a 1:1 rename exists (`hypernym`, `hyponym`,
`antonym`, `derivation`, `causes`, `entails`, `see_also`), store a mapping only for the ones that
split finer (`meronym`→`mero_*`, `holonym`→`holo_*`, `instance_of`→`instance_hypernym`), and keep
`confusable_with`, `used_with`, `collocation` as explicitly project-specific values under an `og:`
namespace (`og:confusable_with`, `og:used_with`, `og:collocation`). GWA/WN-LMF is the de facto
interchange format for wordnet-style resources, so aligning our common relation names buys
interoperability for free; but our `meronym`/`holonym` are deliberately coarse-grained (the
generation pipeline doesn't currently distinguish part/member/substance), and forcing a finer
split now would require re-classifying every existing edge with information the model was never
asked to produce — a generation-pipeline change, not a naming change. The three relations with no
GWA equivalent capture genuinely lexicographic (not purely wordnet-relational) information, so
marking them `og:`-namespaced signals "intentionally ours" to any interoperating consumer.

### 2e. Migration note

WN-LMF's catch-all `meronym`/`holonym` values already exist for the "unspecified part-type" case,
so **no forced migration is needed** for those two — they can be exported as-is under the
catch-all. If `instance_of` → `instance_hypernym` is renamed, that's a straight rename (update
`identity.py`'s edge-id derivation if it embeds the string value); `confusable_with`, `used_with`,
`collocation` should be `og:`-prefixed only in an export layer, not in the Python enum, since
renaming the enum touches `Relation`, `Edge`, and every prompt/test referencing them for no
schema-shape benefit. Net effect: zero required changes to `schema.py`; the crosswalk becomes the
contract for a future WN-LMF/RDF export.

---

## 3. EtymologySegment.language → ISO 639-3 with BCP 47

`EtymologySegment.language` (`src/opengloss_generator/schema.py`) is currently free-text `str`.
Codes fetched live from `https://iso639-3.sil.org/code/<code>` unless noted.

### 3a. Crosswalk — the ~25 dominant etymology-source languages

| Language | ISO 639-3 | BCP 47 | Wiktionary etym. code (if different) | Glottolog | Notes |
|---|---|---|---|---|---|
| Latin | `lat` | `la` | — | — | Well-known individual code. |
| Ancient Greek | `grc` | `grc` | — | — | SIL name "Ancient Greek (to 1453)"; no 639-1 code, BCP47 uses the 639-3 code directly. |
| Old English | `ang` | `ang` | — | — | Individual/historical code; IANA registers `ang` directly. |
| Middle English | `enm` | `enm` | — | — | Same pattern. |
| Old French | `fro` | `fro` | — | — | Same pattern. |
| Anglo-Norman | `xno` | `xno` | — | — | SIL name "Anglo-Norman," scope Historical, individual language — confirmed. |
| Old Norse | `non` | `non` | — | — | SIL name "Old Norse," active in 639-2 and 639-3 — confirmed. |
| Proto-Germanic | **none** | **none** | `gem-pro` | not located as a distinct node | `gem` is the ISO 639-3 **collective/family** code "Germanic languages," *not* a code for the reconstructed proto-language — must never be used as if it were. Wiktionary's etymology-language code is `gem-pro`. No distinct Glottolog languoid for the proto-language (as opposed to the family) was found live — flagged unresolved, not guessed. |
| Proto-Indo-European | **none** | **none** | `ine-pro` | `indo1319` is the attested **family** node ("Indo-European," classification level Family), not a distinct reconstructed-language node; a second node `indo1329` "Indo-European (Unattested)" is classed Pseudo Family but its scope wasn't confirmed live | Confirmed: no ISO 639-3 code exists for PIE. Wiktionary uses `ine-pro`. Glottolog, like ISO 639-3, has no unambiguous dedicated code for PIE-the-reconstructed-language — only the family node. |
| Middle French | `frm` | `frm` | — | — | SIL name "Middle French (ca. 1400-1600)" — confirmed. |
| French | `fra` (bibl. `fre`) | `fr` | — | — | Standard modern-language pattern. |
| Italian | `ita` | `it` | — | — | " |
| Spanish | `spa` | `es` | — | — | " |
| Dutch | `nld` (bibl. `dut`) | `nl` | — | — | " |
| Middle Dutch | `dum` | `dum` | — | — | SIL name "Middle Dutch (ca. 1050-1350)" — confirmed. |
| German | `deu` (bibl. `ger`) | `de` | — | — | Standard modern-language pattern. |
| Old High German | `goh` | `goh` | — | — | SIL name "Old High German (ca. 750-1050)" — confirmed. |
| Arabic | `ara` | `ar` | — | — | Macrolanguage; standard pattern. |
| Hebrew | `heb` | `he` | — | — | Standard pattern. |
| Sanskrit | `san` | `sa` | — | — | " |
| Hindi | `hin` | `hi` | — | — | " |
| Japanese | `jpn` | `ja` | — | — | " |
| Chinese (Mandarin) | `cmn` | `zh` (common) or `cmn` (precise) | — | — | SIL: `cmn` = "Mandarin Chinese," a member of macrolanguage Chinese (`zho`/`zh`). General text commonly tags Mandarin `zh`; `cmn` is valid when precision vs. Cantonese etc. matters. |
| Scottish Gaelic | `gla` | `gd` | — | — | Standard pattern. |
| Welsh | `cym` (bibl. `wel`) | `cy` | — | — | " |

### 3b. Recommendation: store the ISO 639-3 code plus a display name — with a sentinel for the two proto-languages

ISO 639-3 has dedicated, individually-attested codes for every historical/Middle-period language
in this list (`ang`, `enm`, `fro`, `xno`, `non`, `frm`, `goh`, `dum`, `grc`), so a `(code,
display_name)` pair is sufficient and lossless for 23 of the 25 languages, and BCP47 is simply
the ISO 639-3 code itself where no 639-1/639-2 code exists. The two reconstructed proto-languages
(Proto-Germanic, Proto-Indo-European) have no ISO 639-3 code at all, so for these two, store the
de facto standard non-ISO sentinel used by every etymological resource that hits this same wall:
Wiktionary's `gem-pro` and `ine-pro`. This keeps the field's shape uniform (`code +
display_name` everywhere) while being honest that these two codes are not
ISO 639-3/BCP47-conformant — document them as `og:`/Wiktionary-sourced extensions, the same
pattern § 2's `og:`-namespace recommendation uses for non-standard relation types.

### 3c. Migration note

`EtymologySegment.language` is unconstrained free text today, so no schema-breaking change is
needed to start writing conformant codes. Existing string values (e.g. `"Old French"`, `"Latin"`)
should be backfilled via a lookup table mapping display names → codes (the crosswalk above,
extended to full coverage), storing the code alongside the original display string. For display,
prefer the display name; for interoperability/export (RDF, TEI `<usg type="lang">`), use the
code, falling back to the `gem-pro`/`ine-pro` sentinels for the two proto-languages and flagging
any unresolved string for manual review rather than silently coercing it.

---

## 4. EntityType → OntoNotes 5 / ACE + Schema.org

Our enum (`src/opengloss_generator/schema.py`, `EntityType`): `person, place, organization, work,
event, product, species, other`. This field only fires when `LexemeKind.PROPER_NOUN` — it is a
*proper-noun typing* field, not a general-purpose NER tagset.

### 4a. OntoNotes 5 named-entity types (18 types)

The LDC's primary catalog page (LDC2013T19) states OntoNotes English NER covers "3,637 documents
... annotated with 18 named entity types," but its full guideline PDF was not reliably fetchable.
As a secondary source (the same fallback pattern `docs/REGISTERS.md` uses for the OED, §4), spaCy's
own `spacy/glossary.py` source (fetched from
`raw.githubusercontent.com/explosion/spaCy/master/spacy/glossary.py`) reproduces the OntoNotes-5
type descriptions verbatim, since spaCy documents its English NER labels as OntoNotes-5-based:

| Type | Description (verbatim from spaCy's `GLOSSARY`) |
|---|---|
| `PERSON` | "People, including fictional" |
| `NORP` | "Nationalities or religious or political groups" |
| `FAC` (Facility) | "Buildings, airports, highways, bridges, etc." |
| `ORG` | "Companies, agencies, institutions, etc." |
| `GPE` | "Countries, cities, states" |
| `LOC` | "Non-GPE locations, mountain ranges, bodies of water" |
| `PRODUCT` | "Objects, vehicles, foods, etc. (not services)" |
| `EVENT` | "Named hurricanes, battles, wars, sports events, etc." |
| `WORK_OF_ART` | "Titles of books, songs, etc." |
| `LAW` | "Named documents made into laws." |
| `LANGUAGE` | "Any named language" |
| `DATE` | "Absolute or relative dates or periods" |
| `TIME` | "Times smaller than a day" |
| `PERCENT` | 'Percentage, including "%"' |
| `MONEY` | "Monetary values, including unit" |
| `QUANTITY` | "Measurements, as of weight or distance" |
| `ORDINAL` | '"first", "second", etc.' |
| `CARDINAL` | "Numerals that do not fall under another type" |

Not independently cross-checked against the LDC PDF text itself — flagged at the same caveat
level `docs/REGISTERS.md` gives its OED citation.

### 4b. Schema.org `Thing` direct subtypes — verbatim (fetched live from <https://schema.org/Thing>)

"More specific Types": **Action, BioChemEntity, CreativeWork, Event, Intangible, MedicalEntity,
Organization, Person, Place, Product, Taxon.** (The fetched content did not render one-line
descriptions, so only type names are quoted verbatim.)

### 4c. Crosswalk

| Our `EntityType` | → OntoNotes type(s) | → Schema.org type | Notes |
|---|---|---|---|
| `person` | `PERSON` | `Person` | Clean 1:1:1. |
| `place` | `GPE`, `LOC` | `Place` | **One-to-many**: OntoNotes splits political/administrative places (`GPE`) from physical geography (`LOC`). Our single `place` collapses this — lossy our→OntoNotes. |
| `organization` | `ORG` | `Organization` | Clean 1:1:1. |
| `work` | `WORK_OF_ART` | `CreativeWork` | Clean, modulo naming (OntoNotes' scope is narrower than Schema.org's `CreativeWork` tree). |
| `event` | `EVENT` | `Event` | Clean 1:1:1. |
| `product` | `PRODUCT` | `Product` | Clean 1:1:1. |
| `species` | *(no OntoNotes type)* | `Taxon` | OntoNotes has **no NE type for species/organisms** — none of the 18 descriptions cover taxonomic names; would likely go untagged or be force-fit in OntoNotes practice. Schema.org has a dedicated `Taxon` — clean only on that side. |
| `other` | *(residual)* | *(residual — falls back to bare `Thing`)* | Catch-all by design, no standard analogue. |

**OntoNotes types with no analogue in ours** (expected, since ours excludes numeric/temporal
expressions entirely): `NORP`, `LAW`, `LANGUAGE`, `DATE`, `TIME`, `PERCENT`, `MONEY`, `QUANTITY`,
`ORDINAL`, `CARDINAL`. Of these, `NORP` and `LANGUAGE` are the two that plausibly *are* proper
nouns our schema would encounter (e.g. "French" as a nationality/language) but currently have no
home — they'd fall into `other`.

### 4d. Recommendation: leave `EntityType` as is; store a crosswalk only

Our field is deliberately narrower than a general NER tagset — it only fires for
`LexemeKind.PROPER_NOUN` and has no use for numeric/temporal types (`DATE`, `MONEY`, `PERCENT`,
etc. can never apply to a proper noun). The one real gap, `place` collapsing `GPE`/`LOC`, isn't
costly enough to justify a schema change since nothing in the generation pipeline currently needs
that distinction. Adopting OntoNotes' 18-type scheme wholesale would import nine categories that
can never fire for a proper noun, which would violate the "no extra fields" spirit of
`_Base`'s `extra="forbid"` design. The crosswalk table is exactly what's needed to emit
OntoNotes- or Schema.org-typed output without touching the stored enum.

### 4e. Migration note

No migration needed. If an export pipeline is built later, add a pure lookup function (e.g.
`entity_type_to_schema_org(EntityType) -> str`) next to `taxonomy.py`'s existing
`legacy_domain`-style helpers, using the crosswalk table above as its contents; `place` should
default to `Place`/`GPE` (the common case) rather than attempting a runtime physical-vs-political
disambiguation the stored data doesn't support.

---

## 5. Domain roots → Library of Congress Classification / IPTC Media Topics

Our 15 domain roots (`src/opengloss_generator/taxonomy.py`, `ROOTS`): `arts, business, education,
everyday_life, health, history, humanities, language, law_government, mathematics, nature,
people_society, science, sports_recreation, technology`.

### 5a. LCC main classes (verbatim titles)

Direct fetch of `loc.gov/catdir/cpso/lcco/` and the per-letter PDFs returned HTTP 403; the
following is cross-confirmed across two independently retrieved secondary renderings of the same
LCC outline, not one direct primary-document render — flagged for re-verification against the
primary PDF set if exactness matters for a citation.

| Letter | LCC class title |
|---|---|
| A | GENERAL WORKS |
| B | PHILOSOPHY. PSYCHOLOGY. RELIGION |
| C | AUXILIARY SCIENCES OF HISTORY |
| D | HISTORY (GENERAL) AND HISTORY OF EUROPE |
| E–F | HISTORY: AMERICA |
| G | GEOGRAPHY. ANTHROPOLOGY. RECREATION |
| H | SOCIAL SCIENCES |
| J | POLITICAL SCIENCE |
| K | LAW |
| L | EDUCATION |
| M | MUSIC AND BOOKS ON MUSIC |
| N | FINE ARTS |
| P | LANGUAGE AND LITERATURE |
| Q | SCIENCE |
| R | MEDICINE |
| S | AGRICULTURE |
| T | TECHNOLOGY |
| U | MILITARY SCIENCE |
| V | NAVAL SCIENCE |
| Z | BIBLIOGRAPHY. LIBRARY SCIENCE. INFORMATION RESOURCES (GENERAL) |

### 5b. IPTC Media Topics — top-level concepts (live JSON, verbatim)

Fetched directly: `https://cv.iptc.org/newscodes/mediatopic/?lang=en-GB&format=json`
(`dateReleased: 2026-07-02`, © IPTC, CC BY 4.0). `hasTopConcept` lists exactly 17:

| QCode | prefLabel (en-GB) |
|---|---|
| medtop:01000000 | arts, culture, entertainment and media |
| medtop:02000000 | crime, law and justice |
| medtop:03000000 | disaster, accident and emergency incident |
| medtop:04000000 | economy, business and finance |
| medtop:05000000 | education |
| medtop:06000000 | environment |
| medtop:07000000 | health |
| medtop:08000000 | human interest |
| medtop:09000000 | labour |
| medtop:10000000 | lifestyle and leisure |
| medtop:11000000 | politics and government |
| medtop:12000000 | religion |
| medtop:13000000 | science and technology |
| medtop:14000000 | society |
| medtop:15000000 | sport |
| medtop:16000000 | conflict, war and peace |
| medtop:17000000 | weather |

### 5c. Crosswalk: our root → LCC class(es) → IPTC top-level topic(s)

| Our root | LCC class(es) | IPTC top-level topic(s) | Flag |
|---|---|---|---|
| `arts` | **M** Music, **N** Fine Arts | `01000000` arts, culture, entertainment and media | Spans 2 LCC classes |
| `business` | **H** Social Sciences (HB–HJ economics/finance is a subclass) | `04000000` economy, business and finance | LCC has no dedicated top-level letter for business |
| `education` | **L** Education | `05000000` education | Clean 1:1 both ways |
| `everyday_life` | *none cleanly* — fragments across **G** (recreation), **T** (household technology), **S** (agriculture/food) | `10000000` lifestyle and leisure; partial `08000000` human interest | No LCC top-level class corresponds to "everyday life" as a subject — LCC is a shelving taxonomy |
| `health` | **R** Medicine | `07000000` health | Clean 1:1 both ways |
| `history` | **C** Auxiliary Sciences of History, **D** History (General/Europe), **E–F** History: America | partial `08000000` human interest; no dedicated IPTC top-level for history-as-such | Spans 3–4 LCC classes; IPTC (a news taxonomy) has no direct counterpart at all |
| `humanities` | **B** Philosophy/Psychology/Religion, **P** Language and Literature | `12000000` religion (partial); literature nests deep under `01000000` | Spans 2 LCC classes and splits across 2 IPTC top-levels |
| `language` | **P** Language and Literature | nested only as a narrower term under `01000000` | LCC gives language a dedicated letter; IPTC buries it several levels deep |
| `law_government` | **J** Political Science, **K** Law | `02000000` crime, law and justice; `11000000` politics and government | Spans 2 LCC classes and 2 IPTC top-levels — the most naturally two-way-split root |
| `mathematics` | **Q** Science (QA subclass) | `13000000` science and technology | LCC gives math no dedicated top-level letter |
| `nature` | **Q** Science (QC–QR physics/geology/botany/zoology), **G** Geography | `06000000` environment; partial `17000000` weather | Spans 2 LCC classes |
| `people_society` | *none cleanly* — nearest is **H** Social Sciences | `14000000` society | H is an academic-discipline label, not a life-topic category — least-bad fit, not clean |
| `science` | **Q** Science (general Q plus QA–QR specific sciences) | `13000000` science and technology | Effectively spans itself via 10+ subclasses |
| `sports_recreation` | **G** Geography/Anthropology/Recreation (GV subclass) | `15000000` sport | LCC gives sports no dedicated top-level letter |
| `technology` | **T** Technology | `13000000` science and technology | Clean-ish to LCC; many-to-one on the IPTC side (merged with science) |

### 5d. Recommendation: leave our 15 roots as is; store an export-time mapping table only

LCC is a *shelving* taxonomy built to give every physical book exactly one call number, so it
routinely has no top-level slot for whole life-domains our dictionary needs (`everyday_life`,
`people_society` have no clean LCC home; `business`, `mathematics`, `sports_recreation` are
buried as subclasses, not top-level letters). IPTC Media Topics is a *news-event* taxonomy — it
has no equivalent for `history` as a subject and merges `mathematics`/`science`/`technology`/
`nature`'s physical-science parts into one `science and technology` bucket, too coarse for a
K-12-through-college dictionary that (per `taxonomy.py`'s own docstring) was purpose-built to
avoid a "general academic" catch-all. Neither standard answers "what life/subject domain does
this word sense belong to for a learner" the way `ROOTS` does; replacing it with either
standard's top level would be a granularity and coverage regression. Store the crosswalk above
for anyone who wants to filter/export by LCC or IPTC without weakening our own tagging.

### 5e. Migration note

No migration to existing data. If adopted, add a static lookup module mapping each of the 15
`ROOTS` strings to LCC letter(s) and IPTC qcode(s) per § 5c, exposed only at export/interchange
time — never write LCC/IPTC codes into stored `DomainTag` values, since several roots are
legitimately one-to-many on both sides and a single stored code would be lossy in a way the enum
currently is not.

---

## 6. ReadingLevel → CCSS text-complexity bands / Lexile / ATOS / CEFR / MARC

Our enum (`src/opengloss_generator/schema.py`, `ReadingLevel`): `neutral, grade_1, grade_5,
grade_10, college`.

### 6a. Verbatim CCSS 2015-updated grade-band table (multi-measure)

Primary source, fetched and text-extracted directly: achievethecore.org, *"Updated Text
Complexity Grade Bands and Associated Ranges from Multiple Measures"* (the 2015 revision of CCSS
Appendix A's original 2010 scale),
`https://achievethecore.org/content/upload/CCSS_Grade_Bands_and_Quantitative_Measures updated
2015.pdf`.

| Common Core Band | ATOS | Degrees of Reading Power | Flesch-Kincaid | Lexile Framework | Reading Maturity | Text Evaluator |
|---|---|---|---|---|---|---|
| 2nd–3rd | 2.75–5.14 | 42–54 | 1.98–5.34 | 420–820 | 3.53–6.13 | 100–590 |
| 4th–5th | 4.97–7.03 | 52–60 | 4.51–7.73 | 740–1010 | 5.42–7.92 | 405–720 |
| 6th–8th | 7.00–9.98 | 57–67 | 6.51–10.34 | 925–1185 | 7.04–9.57 | 550–940 |
| 9th–10th | 9.67–12.01 | 62–72 | 8.32–12.12 | 1050–1335 | 8.41–10.81 | 750–1125 |
| 11th–CCR | 11.20–14.10 | 67–74 | 10.34–14.2 | 1185–1385 | 9.57–12.00 | 890–1360 |

Quoted from the source: *"The band levels themselves have been expanded slightly over the
original CCSS scale that appears in Appendix A at both the top and bottom of each band to provide
for a more modulated climb toward college and career readiness... This change was provided in
response to feedback received since publication of the original scale (published in terms of the
Lexile® metric) in Appendix A."* **No K–1 row exists** — Appendix A explicitly excludes K–1 from
quantitative grade bands (foundational-literacy texts are evaluated qualitatively), by design.

The original 2010 Appendix A Lexile bands were lower before the 2012 "stretch"/CCR-realignment
(e.g. grades 6–8 were originally ~860–1010L) — this detail is secondary-sourced (search-engine
synthesis), not independently re-verified against a live 2010 PDF; the table above is the
current, post-stretch band and is the one to cite.

### 6b. ATOS

The ATOS column above already gives ATOS ranges per CCSS band from the same primary table.
Supplementary (secondary-sourced, direct fetch of Renaissance's own explainer returned only a
JS-rendered shell): ATOS is reported as **grade.month** (e.g. a book level of 4.5 ≈ average
reader in the fifth month of grade 4), computed via `(avg. sentence length × 0.3905) + (avg. word
length × 11.813) − 15.59`, blended with a book-length adjustment. ATOS numbers are directly
grade-equivalent, unlike Lexile's own non-grade numeric scale.

### 6c. Consolidated crosswalk

CEFR and MARC 008/22 columns reused verbatim from `docs/REGISTERS.md` §6–7c (already established
in this project).

| Our level | CCSS band | Lexile band | ATOS level | CEFR (illustrative, L2 only) | MARC 008/22 | Approx. age |
|---|---|---|---|---|---|---|
| `neutral` | n/a — register-neutral prose, not grade-banded | n/a | n/a | n/a | `g` General | n/a |
| `grade_1` | below the 2nd–3rd band (no quantitative CCSS band for grade 1) | ~200–300L (extrapolated below the 420–820L floor) | ~1.0–2.0 (extrapolated below the 2.75 floor) | A1 | `a`/`b` Preschool/Primary | 6–7 |
| `grade_5` | 4th–5th | 740–1010L | 4.97–7.03 | A2/B1 | `c` Pre-adolescent | 10–11 |
| `grade_10` | 9th–10th | 1050–1335L | 9.67–12.01 | B2 | `d` Adolescent | 15–16 |
| `college` | 11th–CCR | 1185–1385L | 11.20–14.10 | C1/C2 | `e` Adult / `f` Specialized | 18+ |

`grade_1` has no direct CCSS/ATOS/Lexile band of its own (the standards' quantitative scale
starts at "2nd–3rd") — that row is extrapolated below the floor, flagged explicitly rather than
presented as sourced.

### 6d. Recommendation: leave `ReadingLevel` as is; keep the crosswalk as reference only

Lexile and ATOS are *native-reader text-complexity* formulas computed from an existing text's
sentence/word statistics — they can't be assigned to a *planned* gloss before it's written. CEFR
measures *second-language proficiency* (irrelevant to a native-English dictionary). MARC 008/22 is
a *cataloger's audience judgment* for a whole publication, not a per-sentence metric. Our
four-level enum is a simple, generation-time *target* ("write this gloss so a 5th-grader can read
it") the model can act on directly; none of the four external scales can substitute as the stored
field without losing that generative simplicity or requiring a downstream readability-formula
pass. A QA check that scores generated glosses against these bands is a legitimate future feature
— but that's a QA addition, not a schema field change.

### 6e. Migration note

No migration needed. If a QA pass measuring generated text against these bands is added later,
store the *computed* score on `Assessment.readability_grade` (already present as a float) rather
than adding new enum members, using § 6c as the target-range lookup table for that check.

---

## 7. frequency → the Zipf scale

Our schema: `Lexeme.frequency: float | None` (`src/opengloss_generator/schema.py`).

### 7a. Current state (verified by repository grep, not inferred)

`migrate.py` sets `frequency=_v13_frequency(payload.get("wiki_frequency"))`, where
`_v13_frequency` just casts the v1.3 `wiki_frequency` integer to `float` — **no normalization, no
Zipf transform, no per-corpus scaling is applied anywhere in the codebase.** `scripts/
core_lexicon.py` uses `wiki_frequency` only as a raw Wikipedia-derived count for percentile
ranking alongside other raw signals (`in_degree`, OpenSubtitles rank, Google-web rank) — again no
log/Zipf transform. **No field anywhere in `schema.py`, `migrate.py`, or `scripts/` records corpus
size** (grepped `corpus_size`, `total_tokens`, `per_billion`, `zipf` — zero matches). Today,
`Lexeme.frequency` is an un-normalized raw Wikipedia occurrence count with the source corpus's
total token count lost.

### 7b. The Zipf formula

van Heuven, W. J. B., Mandera, P., Keuleers, E., & Brysbaert, M. (2014). "SUBTLEX-UK: A new and
improved word frequency database for British English." *Quarterly Journal of Experimental
Psychology*, 67(6), 1176–1190. DOI: 10.1080/17470218.2013.850521. Fetched via tandfonline.com and
corroborated by wellformedness.com/blog/zipf-scale, which quotes the paper directly:

> Zipf = log₁₀(c) − log₁₀(N) + 9, when raw frequency c > 0, and 0 otherwise

where `c` is the raw occurrence count and `N` is total corpus size in words/tokens — algebraically
`log10(c/N) + 9 = log10((c/N) × 10⁹)`, i.e. **log₁₀(frequency per billion words)**, independently
confirmed by the wordfreq PyPI documentation: *"The Zipf frequency of a word is the base-10
logarithm of the number of times it appears per billion words."* Equivalently, from frequency per
million words (`wpm`): `Zipf = log10(wpm) + 3`.

### 7c. Interpretation bands

Corroborated across the SUBTLEX-UK paper, wordfreq docs, and secondary summaries of Brysbaert,
Mandera & Keuleers (2018), "Word prevalence norms" (paywalled at Springer — cited as the standard
secondary elaboration, not independently re-verified in full text):

| Zipf value | Meaning |
|---|---|
| 1 | ~1 occurrence per 100 million words (very rare) |
| 3 | ~1 occurrence per million words |
| 4 | boundary — low/high frequency split (≤3 = low-frequency; ≥4 = high-frequency) |
| 6 | ~1 occurrence per thousand words; ceiling for ordinary content words |
| 7 | reached only by a handful of function words (*the, you, but, with, have*) |

Reasonable Zipf values for English generally fall between 0 and 7; 0 is the convention for a word
with zero occurrences in the reference corpus (the formula gives 0 for `c = 0`, not `−∞`).

### 7d. Computing Zipf from `wiki_frequency`

`zipf = log10(wiki_frequency / N) + 9`, where `N` is the token count of the Wikipedia dump/
snapshot the count was extracted from. **`N` must be recorded** — the formula is meaningless
without it, and per § 7a our schema currently records neither `N` nor the Wikipedia snapshot/date
`wiki_frequency` came from. Two entries with the same `wiki_frequency` integer from corpora of
different sizes have different true frequencies, and the raw count alone cannot be converted to
Zipf, or compared across a re-scrape, without it.

### 7e. Recommendation

Adopt Zipf as the *stored* value of `Lexeme.frequency`, computed once at ingest time from the raw
count and the corpus size, with the corpus identity (source, snapshot date, and `N`) recorded in
a `Provenance` record for the ingest step rather than living only in a script's local variable.
Zipf is corpus-size-invariant and directly comparable across re-scrapes, across the OpenSubtitles/
Google-web signals already blended in `core_lexicon.py`, and against the published psycholinguistic
literature's frequency norms most reading-level and vocabulary-difficulty tooling expects — a raw
count is comparable to nothing outside its own corpus and silently breaks the moment the corpus is
refreshed.

### 7f. Migration note

Backfilling is possible only if the original raw `wiki_frequency` count *and* the token count of
the specific Wikipedia dump it was computed from can both be recovered (ingest-script logs, dump
metadata, or re-running extraction against the archived dump). If corpus size was never recorded
and the source dump/snapshot cannot be re-identified, the Zipf value cannot be reconstructed from
the stored integer alone — the honest options are (a) leave `frequency` as `None` pending
re-derivation from a fresh corpus pull with size recorded, or (b) if the exact same dump is still
fetchable, re-run extraction end-to-end (raw count + N together) rather than retrofitting N onto
the old count.

---

## 8. Provenance → W3C PROV-O

Our schema (`src/opengloss_generator/schema.py`, `Provenance`): `stage` (`StageName`), `model`,
`prompt_version`, `service_tier`, `input_tokens`, `cached_input_tokens`, `output_tokens`,
`cost_usd`, `attempts`, `run_id`, `note`, `generated_at`.

### 8a. Core PROV-O terms — from <https://www.w3.org/TR/prov-o/>

- **`prov:Entity`** — "An entity is a physical, digital, conceptual, or other kind of thing with
  some fixed aspects; entities may be real or imaginary."
- **`prov:Activity`** — "An activity is something that occurs over a period of time and acts upon
  or with entities; it may include consuming, processing, transforming, modifying, relocating,
  using, or generating entities."
- **`prov:Agent`** — "An agent is something that bears some form of responsibility for an
  activity taking place, for the existence of an entity, or for another agent's activity."
- **`prov:SoftwareAgent`** — "A software agent is running software."
- **`prov:Plan`** — "An entity that represents a set of actions or steps intended by one or more
  agents to achieve some goals."
- **`prov:Association`** — an activity association assigns responsibility to an agent for an
  activity, indicating the agent had a role in it.
- **`prov:wasGeneratedBy`** — "Generation is the completion of production of a new entity by an
  activity."
- **`prov:used`** — "Usage is the beginning of utilizing an entity by an activity."
- **`prov:wasAssociatedWith`** — links an activity to the agent responsible for it.
- **`prov:startedAtTime`** / **`prov:endedAtTime`** — the time an activity started/ended.
- **`prov:generatedAtTime`** — the timestamp at which an entity was completely produced.
- **`prov:qualifiedAssociation`** / **`prov:hadPlan`** — qualification properties that reify an
  `Association` as its own node so it can carry a plan, roles, etc.
- **`prov:value`** — "Provides a literal value that is a direct representation of an entity."

**Confirmed: PROV-O has no native property for cost, token counts, or other numeric usage/
measurement data.** The spec is explicitly designed for extension via additional properties for
exactly this kind of domain-specific metric — a project-specific `og:` extension is the expected
pattern, not a workaround.

### 8b. Field mapping

| Our field | PROV-O mapping | Note |
|---|---|---|
| (the rendition/sense/entry the record is attached to) | `prov:Entity` | the thing generated |
| `stage` (`StageName`) | `prov:Activity`, typed via `prov:type` (e.g. `og:renditions`) | one Activity instance per stage execution |
| `run_id` | identifies the `prov:Activity` instance (its URI) | natural activity identifier |
| `model` | `prov:Agent` (a `prov:SoftwareAgent`), linked via `prov:wasAssociatedWith` | |
| `prompt_version` | `prov:used` on a `prov:Entity` (the prompt as a versioned entity), or `prov:hadPlan` | "used a prompt entity" is the more literal reading |
| `generated_at` | `prov:generatedAtTime` on the Entity (and/or `prov:endedAtTime` on the Activity) | both defensible; `generatedAtTime` is the more direct fit |
| `service_tier`, `attempts` | `og:serviceTier`, `og:attempts` (extension properties) | no PROV-O equivalent |
| `input_tokens`, `cached_input_tokens`, `output_tokens`, `cost_usd` | `og:inputTokens`, `og:cachedInputTokens`, `og:outputTokens`, `og:costUsd` | confirmed no native PROV-O term for any of these |
| `note` | `rdfs:comment` or `og:note` | free text |

### 8c. Turtle example

```turtle
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix og:   <https://opengloss.example/ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

og:rendition/river-n-s0-neutral-plain
    a prov:Entity ;
    prov:wasGeneratedBy og:run/r_8f2a1c ;
    prov:generatedAtTime "2026-08-14T02:11:03Z"^^xsd:dateTime .

og:run/r_8f2a1c
    a prov:Activity, og:renditions ;
    prov:used og:prompt/renditions-v7 ;
    prov:wasAssociatedWith og:agent/claude-sonnet-5 ;
    prov:startedAtTime "2026-08-14T02:10:58Z"^^xsd:dateTime ;
    prov:endedAtTime "2026-08-14T02:11:03Z"^^xsd:dateTime ;
    og:inputTokens 412 ;
    og:cachedInputTokens 180 ;
    og:outputTokens 96 ;
    og:costUsd "0.00187"^^xsd:decimal ;
    og:attempts 1 .

og:agent/claude-sonnet-5 a prov:SoftwareAgent .
```

### 8d. Recommendation

Do not restructure the stored `Provenance` model — it should stay the flat, per-stage record it
is now, since that shape is far cheaper to write and query at this pipeline's volume (one record
per stage per entry) than reified PROV-O triples would be. Adopt the mapping above as the
canonical **export-time** transform: when provenance needs to leave the system for audit,
interop, or an RDF-consuming downstream tool, generate PROV-O triples from the stored records on
the fly, using an `og:` namespace for the fields PROV-O has no native term for. This gets
standards-compliant interoperability without paying its storage/query cost internally.

### 8e. Migration note

No schema change required — this is an additive export path. If adopted, publish a minimal `og:`
namespace document (`https://opengloss.example/ns#` with `rdfs:label`/`rdfs:comment` on each
extension property) so exported Turtle/JSON-LD is self-describing rather than using an
undocumented ad hoc prefix.

---

## 9. Assessment.qa_flags → MQM core error typology

Our schema (`src/opengloss_generator/schema.py`, `Assessment`): `qa_flags: list[str] =
Field(default_factory=list)` — unconstrained free text; `"grammar issue"` and `"grammatical
error"` are different, unaggregatable strings today.

### 9a. What MQM Core says (fetched live)

`themqm.org` 403s to a plain fetch; the equivalent `www.themqm.org` host serves a Next.js app
whose server-rendered HTML embeds the full page text, fetched directly at
`https://www.themqm.org/mqm-pillars/typology/` and `.../the-mqm-core-typology/`. Quoted verbatim:

> "The MQM error typology contains **seven high-level error type dimensions**. Each dimension
> comprises more specific **error subtypes**, structured hierarchically... **MQM Full** is the
> full repository of error types. **MQM Core** is a pre-established, widely used subset of error
> types at the two highest hierarchical levels. It replaces MQM/DQF and can be used as a default
> for maximum comparability."

The seven top-level dimensions, verbatim definitions:

| Dimension | Definition (verbatim) |
|---|---|
| **Terminology** | "errors arising when a term does not conform to normative domain or organizational terminology standards or when a term in the target text is not the correct, normative equivalent of the corresponding term in the source text" |
| **Accuracy** | "errors occurring when the target text does not accurately correspond to the propositional content of the source text, introduced by distorting, omitting, or adding to the message" |
| **Linguistic conventions** (*Fluency* in MQM 1.0) | "errors related to the linguistic well-formedness of the text, including problems with grammaticality, spelling, punctuation, and mechanical correctness" |
| **Style** | "errors occurring in a text that are grammatically acceptable but are inappropriate because they deviate from organizational style guides or exhibit inappropriate language style" |
| **Locale conventions** | "errors occurring when the translation product violates locale-specific content or formatting requirements for data elements" |
| **Audience appropriateness** (*Verity* in MQM 1.0) | "errors arising from the use of content in the translation product that is invalid or inappropriate for the target locale or target audience" |
| **Design and markup** | "errors related to the physical design or presentation of a translation product, including character, paragraph, and UI element formatting and markup, integration of text with graphical elements, and overall page or window layout" |

The two-level MQM Core tree (dimension → subtypes), from the same fetch:

- **Terminology** (3): Inconsistent with terminology resource, Inconsistent use of terminology, Wrong term
- **Accuracy** (7): Mistranslation, Overtranslation, Undertranslation, Addition, Omission, Do not translate, Untranslated
- **Linguistic conventions** (6): Grammar, Punctuation, Spelling, Unintelligible, Character encoding, Textual conventions
- **Style** (7): Organization style, Third-party style, Inconsistent with external reference, Language register, Awkward style, Unidiomatic style, Inconsistent style
- **Locale conventions** (8): Number format, Currency format, Measurement format, Time format, Date format, Address format, Telephone format, Shortcut key
- **Audience appropriateness** (2): Culture-specific reference, Offensive
- **Design and markup** (5, list truncated in the fetch after "Layout, Markup tag")

Content © MQM Council, licensed CC BY 4.0 per the page footer — verbatim reuse here is permitted
with attribution.

### 9b. Applicability to a dictionary-generation pipeline (not translation)

MQM is source→target translation QA; our judge assesses generated dictionary content with no
"source text" being translated. **Applicable, reinterpreted**: Accuracy's *Mistranslation* →
factual error in a gloss/encyclopedia entry; *Addition*/*Omission* → invented unsupported detail
or a dropped required sense component; *Overtranslation*/*Undertranslation* → wrong granularity
for the sense. Terminology's *Wrong term*/*Inconsistent use* → a domain term used incorrectly or
inconsistently with the entry's `DomainTag`. Linguistic conventions (*Grammar*, *Spelling*,
*Punctuation*, *Unintelligible*) apply unchanged. Style's *Language register* → a rendition text
that doesn't match its declared `Register`/`ReadingLevel`; *Awkward*/*Unidiomatic*/*Inconsistent
style* apply directly. Audience appropriateness's *Offensive* → content inappropriate for its
declared reading level; *Culture-specific reference* → an example assuming unexplained cultural
knowledge. **Not applicable**: *Do not translate*/*Untranslated* (no translation step); all eight
*Locale conventions* subtypes (locale-formatting, irrelevant to monolingual English text); all
five *Design and markup* subtypes (no markup/layout layer in our content); *Organization
style*/*Third-party style*/*Inconsistent with external reference* (style-guide conformance with no
analogue here, beyond a coarse `style_violation`-type flag).

### 9c. Recommended closed list

```python
class QAFlag(StrEnum):
    """Closed QA flag vocabulary, each value citing its MQM Core parent type."""

    FACTUAL_ERROR = "factual_error"                    # MQM Accuracy > Mistranslation
    SCOPE_MISMATCH = "scope_mismatch"                   # MQM Accuracy > Overtranslation/Undertranslation
    UNSUPPORTED_ADDITION = "unsupported_addition"       # MQM Accuracy > Addition
    MISSING_CONTENT = "missing_content"                 # MQM Accuracy > Omission
    TERMINOLOGY_ERROR = "terminology_error"             # MQM Terminology > Wrong term / Inconsistent use
    GRAMMAR_ERROR = "grammar_error"                     # MQM Linguistic conventions > Grammar
    SPELLING_ERROR = "spelling_error"                   # MQM Linguistic conventions > Spelling
    PUNCTUATION_ERROR = "punctuation_error"             # MQM Linguistic conventions > Punctuation
    UNINTELLIGIBLE = "unintelligible"                   # MQM Linguistic conventions > Unintelligible
    REGISTER_MISMATCH = "register_mismatch"             # MQM Style > Language register
    AWKWARD_STYLE = "awkward_style"                     # MQM Style > Awkward/Unidiomatic style
    INCONSISTENT_STYLE = "inconsistent_style"           # MQM Style > Inconsistent style
    AUDIENCE_INAPPROPRIATE = "audience_inappropriate"   # MQM Audience appropriateness > Offensive / Culture-specific reference
    HALLUCINATION = "hallucination"                     # og: project-specific — no MQM Core analogue
    OFF_TOPIC = "off_topic"                             # og: project-specific — no MQM Core analogue
    OTHER = "other"                                     # catch-all, requires a note
```

`hallucination` and `off_topic` have no MQM Core parent — a translation typology has no reason to
name "the model invented a plausible-sounding but nonexistent sense/cognate/relation target,"
since a human/MT translator works from real source text and cannot invent a headword. Document
these two as `og:`-namespaced, not MQM-derived.

### 9d. Recommendation

Adopt as a closed `QAFlag` `StrEnum` on `Assessment.qa_flags`. MQM Core is the only widely-cited,
actively maintained (MQM 2.0, CC BY 4.0, aligned to ISO 5060 per the fetched page) closed
typology for this class of problem — text-quality error categorization — and reusing its
dimension names keeps our QA flags legible to anyone who already knows MQM, while the two
additions cover generation-specific failure modes MQM's translation framing doesn't anticipate.
Free-text `qa_flags` cannot be aggregated, filtered, or used to drive targeted re-generation
("show every entry with a `factual_error` flag"); a closed list makes qa_flags queryable and lets
prompt/QA-stage tuning target specific error classes instead of re-reading prose.

### 9e. Migration note

We have no visibility here into the actual distribution of stored `qa_flags` strings, so
migration must be conservative: (1) add `QAFlag` as a new `StrEnum` in `schema.py`; (2) change
`Assessment.qa_flags` from `list[str]` to `list[QAFlag]`; (3) write a one-time migration pass (in
`migrate.py`, alongside the existing v2→v3 upgrade logic) that lower-cases and pattern-matches
existing free-text flags against a lookup table of known synonyms (e.g. `"grammar"`, `"bad
grammar"` → `GRAMMAR_ERROR`); (4) any flag that doesn't match maps to `OTHER`, with the original
string preserved — note `Assessment` has no per-flag note field today, so a bare `OTHER` loses
information; consider whether one is needed. Going forward, ingestion should reject any qa_flag
value outside the enum rather than silently coercing it, consistent with `_Base`'s
`extra="forbid"` philosophy.

---

## 10. Sense alignment → Global WordNet ILI

### 10a. What the ILI is (fetched live from `raw.githubusercontent.com/globalwordnet/ili`)

> "The Collaborative Interlingual Index (CILI) maintains the data for a single interlingual index
> of concepts for wordnets. This repository contains all the data that is available in CILI as
> well as mappings to other resources."

Repository contents: `ili.ttl` (the main index, one definition + source per identifier, Turtle
RDF); `ili-map-pwn30.tab`/`ili-map-wn31.ttl` (Princeton WordNet 3.0/3.1 → ILI mappings);
`ili-map-odwn13.ttl` (Open Dutch WordNet mapping); `sense-mappings/` (mappings between synsets and
sense IDs).

**ID format**, directly from `ili.ttl`:

```turtle
<i1>	a	<Concept> ;
	skos:definition	"(usually followed by `to') having the necessary means or skill or know-how or authority to do something"@en ;
	dc:source	pwn30:00001740-a .
```

An ILI id is the literal string `i` followed by a decimal integer (`i1`, `i2`, ... no leading
zeros, monotonically assigned, never reused), resolvable relative to
`http://globalwordnet.org/cili/`. Each concept is typed `<Concept>` or `<Instance>`, carries one
`skos:definition` (borrowed from whichever wordnet proposed it, usually Princeton WordNet), and
one `dc:source` pointing at the originating synset.

**Status/versioning**, from `VOCABULARY.md`: every concept has exactly one status — `active`
("accepted and in current use... default"), `provisional` ("proposed via a wordnet upload...
OMW grants new concepts this status for roughly a month before they're accepted or... rejected"),
or `deprecated` ("no longer recommended for use... The ID itself is retained rather than deleted,
so it is never recycled," optionally carrying `ili:supersededBy` links). IDs are stable,
append-only identifiers, never recomputed per release.

**How a wordnet references an ILI id**, from the WN-LMF schema (<https://globalwordnet.github.io/schemas/>):

```xml
<Synset id="example-en-10161911-n" ili="i90287" partOfSpeech="n"
        members="example-en-10161911-n-1 example-en-1-n-1">
    <Definition>
        the father of your father or mother
    </Definition>
</Synset>
```

A synset carries its own local id *and* an `ili` attribute pointing at the shared cross-resource
concept. Two wordnets whose synsets both carry `ili="i90287"` assert they denote the same
concept — shared reference is the entire alignment mechanism, not merged records.

### 10b. Open English WordNet (OEWN) as the practical alignment target

From `raw.githubusercontent.com/globalwordnet/english-wordnet/main/README.md`: OEWN is "a fork of
the Princeton WordNet developed under an open source methodology... Correspondence to previous
versions and wordnets in other languages is provided through the Collaborative Interlingual Index
(CILI)." It ships as GWN-LMF XML/JSON/RDF (Turtle)/WNDB, 2025 edition covering 161,875 words /
120,564 synsets / 419,226 relations, free at `en-word.net`. Since Princeton WordNet isn't as
freely redistributable in derivative products, and OEWN already carries `ili="..."` on every
synset, **OEWN is the practical alignment target**, not the ILI registry directly — link by
finding the OEWN synset whose members/gloss best match a sense and reading off its `ili`
attribute (`ili.ttl`'s own `skos:definition` is sparse/borrowed, not a primary lexical resource to
match against directly).

### 10c. How our schema would reference it

`Sense` (`schema.py`) is identified by a derived, positional `sense_id` and currently has no
external-alignment field. The natural addition:

```python
class Sense(_Base):
    ...
    concept_id: str | None = None      # already exists — internal/project concept clustering
    ili_id: str | None = None          # NEW: e.g. "i90287", from a GWN ILI/OEWN linking pass
```

This should **not** be generated by the model during authoring — it requires an external lookup
(matching our canonical gloss + examples against OEWN synset glosses/members, an alignment/WSD
problem in its own right), so it belongs to a dedicated post-hoc linking stage (a new
`StageName`, e.g. `LINK_ILI`, alongside `RESOLVE`/`TAG_DOMAIN`), run after senses are stable.
Validate with `^i[1-9][0-9]*$` (the format observed in `ili.ttl`), stored alongside a `Provenance`
record noting the OEWN release and matching method — the same trust-provenance pattern
`wikidata_qid` already uses on `ProperNounInfo`.

### 10d. Recommendation

Store a mapping only (an optional `ili_id` field, populated by a dedicated linking stage), not a
generated value. ILI ids are meaningless without the source wordnet they were derived from and
can only be assigned correctly by comparing our sense's gloss/examples against an existing
synset inventory — asking a generation-stage model to invent one is asking it to hallucinate a
lookup result. Treating it as an enrichment-stage field keeps the failure mode contained: an
unlinked sense is simply `ili_id: None`, not an invalid entry, and it can be (re-)populated
independently as OEWN itself is updated (annual editions) without touching anything else about
the sense.

### 10e. Migration note

Purely additive: add `ili_id: str | None = None` to `Sense` with no validator requiring it, so
every existing stored entry remains valid as-is. No existing data changes. Linking is a new,
independent enrichment pass — run lexeme-by-lexeme against a chosen OEWN release, record the
release version and match method in a `Provenance` entry, and leave unmatched senses with
`ili_id: None` rather than blocking on 100% coverage, since not every project-specific sense
(e.g. a fine-grained pedagogical split) will have a clean 1:1 OEWN counterpart.

---

## 11. Prioritized summary

### Adopt as stored values now (three)

1. **`frequency` → Zipf scale (§7).** This is the clearest actual defect found: the field is
   silently un-normalized (a raw Wikipedia count with the corpus size never recorded), which
   breaks the moment the source corpus is refreshed and is incomparable to any published
   frequency norm. Compute `zipf = log10(wiki_frequency / N) + 9` at ingest and record `N` (and
   corpus identity) in provenance. This is a correctness fix, not a style preference.
2. **`Assessment.qa_flags` → closed `QAFlag` enum grounded in MQM Core (§9).** Free-text QA flags
   cannot be aggregated or used to drive targeted fixes. A closed, MQM-derived list (plus two
   project-specific additions for generation-only failure modes) makes QA data queryable at
   negligible cost, and fits the project's existing preference for closed vocabularies over free
   text (the same rationale `taxonomy.py`'s docstring gives for replacing v1.x's free-text
   `domain` field).
3. **`EtymologySegment.language` → ISO 639-3 code + display name (§3).** Currently unconstrained
   free text on a field that already has a small, enumerable universe of ~25 recurring values;
   storing `(code, display_name)` costs nothing at generation time, fixes silent
   inconsistency (`"Old French"` vs `"old french"` vs `"OFr."`), and is immediately useful for
   any downstream cross-referencing. The two proto-languages without an ISO code use the
   Wiktionary `gem-pro`/`ine-pro` sentinel, documented as non-ISO.

### Keep as export-time mappings only (six)

4. **`PartOfSpeech` → UPOS/LexInfo (§1)** — our tagset answers a dictionary-sense question UD's
   token-level tagset doesn't; store the crosswalk for CoNLL-U-adjacent export.
5. **`RelationType` → GWA/WN-LMF + SKOS (§2)** — mostly aligns already; rename `instance_of` if
   convenient, but the coarse `meronym`/`holonym` and the three `og:`-only relations
   (`confusable_with`, `used_with`, `collocation`) are legitimately ours.
6. **`EntityType` → OntoNotes/Schema.org (§4)** — a narrower, proper-noun-only field by design;
   nine OntoNotes types (dates, money, etc.) can never apply here.
7. **Domain roots → LCC/IPTC (§5)** — both standards answer different organizing questions
   (library shelving; news topics) than "what life/subject domain for a K-12 dictionary sense,"
   and several roots are legitimately many-to-one or many-to-many against both.
8. **`ReadingLevel` → CCSS/Lexile/ATOS/CEFR/MARC (§6)** — ours is a generation-time target;
   the external scales are post-hoc text-complexity measurements. Reserve their crosswalk for a
   future QA check written to `Assessment.readability_grade`, not a schema change.
9. **`Provenance` → PROV-O (§8)** — keep the flat internal storage shape; the mapping and Turtle
   example are the contract for an RDF/PROV export, not a reason to restructure storage.

### Skip / additive-only, no action required now (one)

10. **Sense alignment → GWN ILI (§10)** — a real, valuable, but *optional and additive* future
    enrichment (`Sense.ili_id: str | None`), populated by a dedicated linking stage against Open
    English WordNet, never generated by the authoring model. No schema urgency; add the field
    only when an ILI-linking stage is actually being built.

### Cross-cutting note

Three of the sections above (§2's `og:confusable_with` etc., §3's `gem-pro`/`ine-pro`, §9's
`hallucination`/`off_topic`) converge on the same pattern: an `og:`-prefixed extension is the
right way to mark "intentionally project-specific, not a gap against the standard." Adopting that
convention consistently — in documentation now, and in any future RDF/JSON-LD export — would give
every one of these fields a clean, honest boundary between "this is X" and "this is our own
extension of X."

## Sources consulted

1. Universal Dependencies UPOS v2 — <https://universaldependencies.org/u/pos/>
2. LexInfo 3.0 ontology — <https://lexinfo.net/> and PreMOn vocabulary reference, <https://premon.fbk.eu/apidocs/eu/fbk/dkm/premon/vocab/LEXINFO.html>
3. Global WordNet Association schemas / WN-LMF DTD — <https://globalwordnet.github.io/schemas/>
4. W3C SKOS Reference — <https://www.w3.org/TR/skos-reference/>
5. ISO 639-3 registry (SIL International) — <https://iso639-3.sil.org/>
6. Wiktionary etymology-language codes (`gem-pro`, `ine-pro`) — <https://en.wiktionary.org/>
7. Glottolog — <https://glottolog.org/>
8. spaCy glossary (OntoNotes-5-based NE labels) — <https://raw.githubusercontent.com/explosion/spaCy/master/spacy/glossary.py>
9. Schema.org `Thing` — <https://schema.org/Thing>
10. Library of Congress Classification Outline — <https://www.loc.gov/catdir/cpso/lcco/> (secondary-confirmed; primary fetch 403'd)
11. IPTC Media Topics NewsCodes — <https://cv.iptc.org/newscodes/mediatopic/>
12. Achieve the Core, CCSS updated text-complexity grade bands (2015) — <https://achievethecore.org/content/upload/CCSS_Grade_Bands_and_Quantitative_Measures%20updated%202015.pdf>
13. `docs/REGISTERS.md` §6–7c (CEFR/MARC crosswalk, reused, not refetched)
14. van Heuven, Mandera, Keuleers & Brysbaert (2014), "SUBTLEX-UK," *QJEP* 67(6) — DOI 10.1080/17470218.2013.850521
15. wordfreq (Robyn Speer) documentation, Zipf frequency definition
16. W3C PROV-O — <https://www.w3.org/TR/prov-o/>
17. MQM Council typology — <https://www.themqm.org/mqm-pillars/typology/>, <https://www.themqm.org/mqm-pillars/the-mqm-core-typology/>
18. Global WordNet Association ILI repository — <https://github.com/globalwordnet/ili> (raw content)
19. Open English WordNet — <https://github.com/globalwordnet/english-wordnet>
