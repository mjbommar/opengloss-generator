# Named-entity plan — tier 6, the names v2.2 does not have (2026-09-08)

Status when this plan was written: **OpenGloss v2.2** shipped today — 148,292 live
lexemes, 288,304 live senses, WordNet lemma coverage ≈ 73%. **20,743** of those lexemes
are `proper_noun`, and only **3,720** of them are multi-word. The store knows
*Washington* (5 senses) and *Lincoln* (3), *braun*, *brecht*, *knievel*; it does not know
**George Washington**, **Abraham Lincoln**, **New York City**, **Supreme Court of the
United States**, **World War II**, or **Yellowstone National Park**. Every tier so far
was selected by *word frequency* (`core_lexicon.py`) or by *WordNet membership* (tier 5),
and neither signal ranks names: a name's importance is a fact about the world, not about
a corpus.

This plan proposes **tier 6**: a ranked candidate list of named entities, the sources and
score that produce it, the filters that keep the lexicon out of trouble, the schema work
it needs, and what it costs.

Deliverables already in the tree:

| artifact | what |
|---|---|
| `scripts/build_tier6_candidates.py` | reproducible builder, zero model calls, zero cost |
| `data/core/tier6_candidates.tsv` | the list — 15,000 rows (gitignored, like every other tier file) |
| `data/core/tier6_cache/` | the 23 MB of raw fetched JSON the builder caches, so a re-run is offline and free |
| `docs/tier6_top200.md` | the top 200, checked in as the preview of the gitignored TSV |

---

## 1. Sources and signals

Everything below was fetched on **2026-09-08** and is in `data/core/tier6_cache/`
(23 MB total). Nothing multi-gigabyte was downloaded.

| # | source | what it gives | volume fetched | licence |
|---|---|---|---|---|
| 1 | **English Wikipedia "vital articles"**, `Wikipedia:Vital articles/data/{A..Z,others}.json` | `title → {level 1-5, topic, section}` — a hand-curated importance ladder | 27 JSON pages, **6.1 MB**, **49,837 titles** | CC BY-SA 4.0 (we take titles and levels, no article text) |
| 2 | **Wikipedia Action API**, `prop=pageprops&ppprop=wikibase_item\|disambiguation&redirects=1` | `title → QID`, redirects resolved, disambiguation flagged | 1,285 batched requests (50 titles each), 7.3 MB cached: **64,228 titles queried**, 49,838/49,840 vital and 12,388/14,391 WordNet resolved, **59,229 distinct QIDs** including the US lists | CC0 (the mapping); API terms |
| 3 | **Wikidata SPARQL** (`query.wikidata.org`), candidate items only | per item: `wikibase:sitelinks`, `P31` instance-of, `P17` country, `P27` citizenship, `P569`/`P570` birth/death, `P571` inception | 149 queries (400 items each) + 28 class-resolution queries, 8.2 MB cached, **59,228 items, 8,459 distinct `P31` classes** | CC0 |
| 4 | **Wikidata SPARQL**, 26 curated **US-specific list queries** | presidents, vice presidents, SCOTUS justices, cabinet secretaries, Nobel laureates, the 50 states, state capitals, every US city ≥ 100K, national parks and monuments, federal executive departments and agencies, S&P 500, Dow 30, Ivy League, US universities (≥ 15 sitelinks), NFL/NBA/MLB/NHL/MLS teams, US battles and conflicts, constitutional amendments, every sovereign country | 26 queries, 0.68 MB, **5,642 distinct titles** | CC0 |
| 5 | **Wikimedia pageviews REST**, `top-per-country/US/all-access/{day}` | how many of 14 sampled days a title was in the **top 1,000 read in the United States** | 14 requests, 0.19 MB, **7,528 distinct titles** | CC0 |
| 6 | **WordNet 3.0 instance synsets** (NLTK, already installed) | 7,730 instance synsets → **14,391 lemmas**, each with its instance hypernym (`president_of_the_united_states`, `city`, `river`, `pitched_battle`…) | offline | WordNet License (`LICENSES/WordNet.txt`) |
| 7 | **`data/hf/opengloss-v2.2-lexicon/data/*.parquet`** | the 152,859 store headwords (`lexeme_id`, `kind`, `retired`) for dedupe | local | ours |
| 8 | **`data/core/light.parquet`** | 205,988 v1.3 headwords, for the `in_v13` column | local | ours |

### Signals evaluated and *not* used

* **Incoming-link count from a Wikipedia SQL dump.** `enwiki-latest-pagelinks.sql.gz` is
  **7.13 GB** (HEAD, 2026-09-08); `enwiki-latest-page.sql.gz` is another 2.39 GB and is
  needed to resolve the ids. That is out of scope for "fetch it now", and Wikidata
  sitelink count is a serviceable substitute for the same idea (how much of the world
  bothered to write about this).
* **Monthly global top-1000 pageviews** (`/metrics/pageviews/top/en.wikipedia/...`) is
  reachable and was sampled, but it is dominated by the news cycle and by cross-project
  noise (`Main Page`, `Especial:Pesquisar`). The **per-country US** endpoint (source 5)
  is the same idea with the geography we actually want, so only that one is scored.
  Note it rejects `all-days`, so it must be sampled day by day.
* **Wikipedia `top-per-country` for a whole month.** Returns HTTP 400; the endpoint is
  per-day only. Fourteen days were sampled across a year instead.
* **Common Core / NAEP civics and history vocabulary.** No free, structured,
  machine-readable list of *named entities* exists: the Common Core ELA standards name no
  entities at all, and the NAEP US-history framework is a PDF of topic descriptions, not
  a list. The 26 Wikidata list queries (source 4) reconstruct the same coverage —
  presidents, states, branches of government, amendments, major wars — from a source that
  *is* structured and CC0. **AP Stylebook** lists are copyrighted and not free.
* **Fortune 500.** No free structured list; **S&P 500** membership (`P361 Q242345`,
  522 companies) and the **Dow 30** are the CC0 stand-ins.

### What each source contributes to the final 15,000

| source combination | rows |
|---|---|
| vital articles only | 10,439 |
| vital + WordNet | 3,421 |
| vital + a US list | 681 |
| vital + WordNet + a US list | 449 |
| WordNet + a US list, not a vital article | 10 |

The vital-article list is doing nearly all the recall work; WordNet and the US queries
are doing the *typing* and the *US weighting*. That is the intended division of labour:
the vital list is broad but says nothing about entity type or about America, and WordNet
and Wikidata say both but are much narrower.

---

## 2. Scoring

One additive score in points, no normalisation, every term inspectable — the TSV carries
a `score_terms` column that shows the arithmetic for each row
(`vital3=80;sitelinks295=29.7;wordnet=15;us=12;uslist:us_president=15;usviews14/14=6.0`).

```
importance =  VITAL[level]                        # 1→100, 2→90, 3→80, 4→60, 5→40, none→0
            + 12 · log10(1 + sitelinks)           # Wikidata sitelink count
            + 15   if the name is a WordNet instance lemma
            + 12   if country/citizenship is the United States
            +  6   else if it is UK / Ireland / Canada / Australia / New Zealand
            + 15   if on a narrow curated US list  (presidents, states, SCOTUS,
                                                    S&P 500, a pro team, a national park…)
            +  8   if only on a broad membership list (every Nobel laureate, every
                                                    US city ≥ 100K, every country)
            +  6 · (days in the US top-1000 / 14)
            - 10   if a living person (no P570 and born ≥ 1935)
            - 10   if inception ≥ 2015
            -  8   if the title is over 34 characters or over 4 words
```

**Why these weights.**

* *Vital level is the backbone* because it is the only signal in the set that is a direct
  human judgment of importance, and its ladder is steep on purpose: one level-3 article
  outranks any amount of sitelink evidence a level-5 article can accumulate (80 vs. a
  practical sitelink ceiling of ~31). Level 4 → 5 is the widest gap (60 → 40) because
  that is where "an educated person should know this" turns into "an encyclopedia should
  cover this".
* *Sitelinks* are log-scaled because the distribution is heavy-tailed: 300 sitelinks
  (`France`) earns 29.8, 30 earns 17.8, 5 earns 9.3. Linear scaling would let a handful
  of super-articles swamp the level ladder.
* *WordNet at 15* is worth roughly a quarter of a vital level, and it is the one signal
  that says "a lexicographer thought this belonged in a **dictionary**", which is exactly
  the question this tier asks.
* *US at 12, anglophone at 6* — the brief is importance to English speakers, especially
  in the United States, so the boost is real but must not outrank a level. It applies to
  4,588 of the 15,000 rows; the anglophone half applies to 1,349.
* *Narrow list 15 / broad list 8.* "One of the 47 presidents" and "one of ~900 Nobel
  laureates" are not the same claim. Splitting them is what pushed Irving Langmuir and
  Robert Burns Woodward back out of the top 200 and let Mecca and Middle East in.
* *Pageviews capped at 6* — deliberately the smallest term. It is the only recency-biased
  signal in the set and it never introduces a candidate on its own; it only breaks ties
  among names that already have a vital level. 1,024 rows earn any of it.
* *Penalties* are small and few by design: 612 living-person penalties, 214 long-title,
  4 recency. They are tie-breakers against instability, not filters — a filter that
  actually removes something is in § 3.

### Sanity check — the top 200

Rendered in full in [`tier6_top200.md`](tier6_top200.md). The first 30 are:

> Abraham Lincoln · George Washington · United States · New York City · Albert Einstein ·
> Earth · United Kingdom · Michael Jackson · Canada · Australia · Mexico · Ronald Reagan ·
> Japan · Hawaii · Israel · Iran · Europe · Moon · North America · Africa · Thomas
> Edison · Asia · Sun · Theodore Roosevelt · Walt Disney · South America · California ·
> India · Henry Ford · Nikola Tesla

and the run to 200 continues through *France, Richard Nixon, Saudi Arabia, Thomas
Jefferson, Nigeria, the Philippines, Nelson Mandela, London, Adolf Hitler, Pennsylvania,
Boston, Denver, Joseph Stalin, Yellowstone National Park, Moscow, Antarctica, Leonardo da
Vinci, Hong Kong, Jerusalem, Mahatma Gandhi, Venus, the Atlantic Ocean, Galileo Galilei,
Vincent van Gogh, Immanuel Kant, Sigmund Freud, the Nile, Elizabeth I, the Ottoman Empire,
the Sahara, the Himalayas, John Locke, Delhi, Moses, Las Vegas, the Byzantine Empire,
Seattle, Dmitri Mendeleev*. There is nothing in the first 200 a US high-school graduate
would fail to recognise, and no footballers.

### Balance

`TYPE_QUOTA` caps each type's share of the cut, as a **ceiling** rather than a target —
the shares sum to 1.13, so a type that runs out of good candidates hands its slack to the
next-best rows overall instead of being padded out with residue. Without the cap, `person`
alone would be ~55% of the list, because vital level 5 holds 14,209 biographies.

| type | cap | selected at 15,000 | share | lowest score in type |
|---|---|---|---|---|
| person | 42% | 6,300 | 42.0% | 67.2 |
| place | 26% | 3,900 | 26.0% | 66.4 |
| work | 15% | 2,031 | 13.5% | 54.5 |
| organization | 15% | 1,553 | 10.4% | 54.5 |
| event | 10% | 926 | 6.2% | 54.5 |
| other | 5% | 290 | 1.9% | 54.5 |

`person` and `place` bind against their caps; the other four do not, so the marginal row
in each of them sits at the same global floor (54.5). **81.9% of the list is
multi-word** — which is the point of the tier.

---

## 3. Filters

64,545 candidate names entered; **37,342** survived; 15,000 were selected. What was
dropped, and why:

| rejection | rows | why |
|---|---|---|
| `untyped` | 8,476 | neither Wikidata, WordNet, nor the vital topic could say what kind of thing it is. Mostly abstract vital articles (*Quinine*, *Quadrilateral*) |
| `common_noun_already_in_store` | 7,079 | a single-word title the store already holds as `simplex`/`compound`/`function_word`. *Earth*, *Life*, *Land*, *Time* are vital articles whose capitalisation is Wikipedia's, not English's. This is a **type error, not a duplicate**: the lexicon has the word and it is not a name |
| `wikidata_concept` | 3,479 | every `P31` this item has names a *category* — "academic discipline", "type of chemical entity", "family name", "occupation", "music genre" |
| `capitalised_concept_not_a_name` | 2,579 | English marks names orthographically, and a Wikipedia title capitalises only its first word unless the rest of the phrase is itself a name — so *Injection moulding*, *Neutron radiation*, *Jensen's inequality*, *Climate change*, *Square root* and *Freedom of speech* are common-noun phrases with a capital letter. Two explicit exceptions: a WordNet instance lemma is always kept, and a multi-word title is kept when its **first** token is not itself an ordinary English word in the store's own vocabulary *and* its type is not `other` — which lets *Apollo program*, *Han dynasty*, *Amazon rainforest*, *Chernobyl disaster*, *Eid al-Fitr* and *Musée d'Orsay* through while keeping *English language* and *Māori people* out. A single-token title carries no orthographic evidence at all and is decided by its Wikidata type instead |
| `taxon_excluded_at_tier5` | 2,473 | Linnaean taxa and organisms, explicitly out of scope since tier 5 |
| `wikidata_disambig` | 2,021 | `P31 = Q4167410` (disambiguation page) or `Q22808320` (human-name disambiguation). *Mercury*, *Washington (disambiguation)* |
| `list_or_meta_title` | 631 | `List of…`, `Outline of…`, `Timeline of…`, `History of…`, `Glossary of…`, and any `Namespace:` prefix |
| `title_too_long` | 352 | over 48 characters or over 6 words — an article subject, not a headword |
| `dated_article_title` | 73 | level-5 titles that begin with a year (*2011 Tōhoku earthquake…*): recent-news shape |
| `wikidata_list` | 34 | `P31 = Q13406463` (Wikimedia list article) |
| `no_letters` / `(disambiguation)` suffix | 6 | |

**Living private individuals.** No candidate in this pool is a private individual: every
one has a Wikipedia article, and every source is a curated list of public figures.
Living *public* figures are not excluded, but they carry the −10 stability penalty
(612 rows), which pushes most of them below the cut. If a harder rule is wanted, the
builder already reads `P570`/`P569` and a `--no-living` flag is a two-line change.

**Recent-news-only entities.** Handled by three things at once: the vital list is edited
on a multi-year cadence and does not chase news; the `inception ≥ 2015` penalty; and the
`dated_article_title` rejection. The pageview signal — the one place the news cycle gets
in — is capped at 6 points and cannot introduce a candidate.

**Dedupe against the store.** Every name is slugified with the same rule as
`identity.slugify` (NFKD → ASCII → lowercase → non-alphanumeric runs to `_`) and looked
up in the exported lexicon parquet. **2,282 of the 15,000 are already live in the store**
(as single-word proper nouns: *Canada*, *Hawaii*, *California*, *Boston*); they stay in
the list with `in_store=1` because they need *entity typing and alias work*, not
generation. **12,718 are new.**

**Single-word surnames that should be linked, not duplicated.** 9,475 rows carry a note
of the form `alias_of candidate: store has 'lincoln'` — the store already has the last
token of the multi-word name as its own lexeme. These are the edges tier 6 exists to
create: `Abraham Lincoln —see_also→ Lincoln`, and, where the store's single-word entry is
genuinely the *same* referent rather than a homonym, `Lincoln —alias_of→ Abraham Lincoln`.
Deciding which of the two it is per row is a per-entry judgment and belongs to a cheap
nano pass, not to this list — the note records the opportunity.

---

## 4. What the schema needs

Schema v3 already has more than the store uses. `LexemeKind.PROPER_NOUN` exists;
`ProperNounInfo` exists with an `entity_type` and an optional `wikidata_qid`; `EntityType`
already enumerates `person, place, organization, work, event, product, species, other`,
with `ONTONOTES_MAP` and `SCHEMA_ORG_MAP` alongside it. The work is not new fields, it is
**filling in what is currently a placeholder** plus three genuine gaps.

**a. `entity_type` is a lie today.** D-12 and D-18 both assign `EntityType.OTHER` to every
migrated proper noun, and `wordnet_import.py:602` does the same for every WordNet-derived
one — so all 20,743 live proper nouns in v2.2 carry `other`. The tier-6 TSV *has* the
answer for its rows (from Wikidata `P31`, WordNet's instance hypernym, or the US list a
name came from), so the import path should write it rather than defaulting. For the
20,743 already in the store, the fix is a `retrofit --only entity_type` pass fed by the
same TSV, free for any row the TSV covers and one nano call for the rest.

The eight members mostly hold up against a real pool. Two observations from typing 64,545
names against them: `EntityType.PRODUCT` is never reached by any source in this tier (a
product name is a `work` or an `organization` to Wikidata), and there is **no member for a
language, a script, a calendar or an ethnic group** — *English language*, *Cyrillic
script*, *Yoruba people*, *Gregorian calendar* all land in `other`, which is why the
`other` bucket needs the tightest filter of the six (§ 3). Fictional and mythological
characters are typed `person` here, following OntoNotes, whose PERSON is explicitly
"people, including fictional" (STANDARDS.md § 4a) — worth writing into `EntityType`'s
docstring, since nothing there says so today.

**b. `wikidata_qid` should be written, and it is free.** Every row of the TSV carries the
QID that produced its type and sitelink count. Storing it makes the entity typing
auditable and gives every future name pass a join key. `ProperNounInfo.wikidata_qid`
already validates `^Q[1-9][0-9]*$`.

**c. Alias and variant handling is the real gap.** v3 has no alias mechanism for a
lexeme. Names need at least four kinds of variant, and today all four would have to be
separate lexemes or `see_also` edges:

| variant | example |
|---|---|
| short form ↔ full form | *Lincoln* / *Abraham Lincoln*; *FDR* / *Franklin D. Roosevelt* |
| initialism ↔ expansion | *NASA*, *FBI*, *NATO*, *UN* — these are `LexemeKind.ABBREVIATION` today, 443 of them, unlinked to the organisation |
| leading article | *the Netherlands*, *the Bronx*, *the Beatles* |
| diacritics and transliteration | *Curaçao* / *Curacao*, *Beyoncé*, *Lao Tzu* / *Laozi* / *Lao Zi* |

`slugify` folds diacritics already, so *Beyoncé* and *Beyonce* collide on one id for free
— which is right, but means the *display* form must be the headword and cannot be
recovered from the id. The minimal change is a `Lexeme.aliases: list[str]` (surface forms
that resolve to this entry, no senses of their own) plus a `RelationType.ALIAS_OF` for the
case where a *separate* lexeme should point here. `RelationType.SEE_ALSO` exists and is
the fallback; it is weaker, because `relation-reconcile` demotes and prunes `see_also`
edges (D-65, D-68) and an alias must not be prunable.

**d. The domain taxonomy has no leaf for a settlement or a polity.** For the tier-6 types:

| entity type | leaf that fits | note |
|---|---|---|
| person (historical) | `history.historical_figures` | good fit, exists |
| place (physical) | `nature.landforms`, `nature.water_bodies` | good fit, exists |
| place (**a city, a state, a country**) | — | **gap.** There is no `geography` root; the closest is `people_society.community_life`, which is about community, not about Denver. 3,900 rows — 26% of the tier — land here |
| organization (government) | `law_government.government_structure`, `.courts_justice`, `.civics` | good fit |
| organization (company) | `business.general`, `.trade_commerce` | acceptable |
| organization (university) | `education.higher_education` | good fit |
| work (book, film, painting, music) | `humanities.literature`, `arts.film`, `arts.visual_art`, `arts.music` | good fit |
| event (war, battle) | `history.world_wars`, `.revolutions`, `.modern_history` | good fit |
| other (deity, myth figure) | `humanities.mythology`, `humanities.religion` | good fit |

Adding a 16th root is a breaking change to `ROOTS` and to every test that pins it, so the
honest options are (i) accept `people_society.community_life` for settlements and say so,
or (ii) add `nature.settlements`/`law_government.polities` as leaves under existing roots.
Option (ii) is cheaper and should be the proposal. **This is a decision the tier needs
before stage 1, not after.**

**e. WordNet's instance hypernym is the `hypernym` target, and D-78 already does this.**
`import-wordnet` maps `instance_hypernyms` to a `RelationType.INSTANCE_OF` edge and to
`hypernym`/`hyponym` from the ordinary pointers. For the 11,120 tier-6 rows WordNet does
*not* cover, the seeded generate path (§ 5) should pass the type-appropriate hypernym in
the prompt — *Denver is a **city***, *Yellowstone is a **national park*** — so the model
writes the same shape of gloss WordNet does rather than inventing one.

**f. What the enrichment chain must *not* do to a name.**

* **Register renditions are wrong for a name and should be skipped.** A `Register` is a
  property of *how you say something*, and a name has no informal/technical/marketing
  variant — "Abraham Lincoln, informally" is either a nickname (which is an *alias*, § 4c)
  or invention. Reading-level renditions are fine and are the point: a `grade_1` gloss of
  *Abraham Lincoln* is a real and useful thing. So: `enrich --fields gloss
  --reading-levels …` yes, `--registers …` no, and the recipe should not cross them.
* **The headword-initial gloss rewrite must stay skipped**, which D-30 already arranges
  by exempting `LexemeKind.PROPER_NOUN` in both the hygiene pass and `audit.py`. A name's
  definition legitimately opens with the name.
* **`sense-hygiene`'s `distinctness` step needs care**, not exemption: a name is usually
  monosemous, and D-52 already makes a one-sense entry cost $0, so it is free and safe.
* **`contrasts` is near-worthless for names** — "how do *Denver* and *Boulder* differ" is
  a geography question, not a lexical one — and should be off for tier 6, as should
  `queries`' register axis.
* **Etymology is valuable and unusual**: for a name it is the *naming* history
  (*Pennsylvania* = Penn + sylvania), which is exactly the content a dictionary carries
  and an encyclopedia does not.

---

## 5. Cost and plan

### Size and cost

Two rates, both from this repository's own record:

* **$0.003/entry** — tier 5's *measured* all-in rate for recipe A over 43,652 entries
  ($127.94 total, TIER3-PLAN § 10). It covers the structural passes, `resolve`, the
  hygiene passes and the levelled gloss/example/encyclopedia renditions. It does **not**
  cover `generate`, because every tier-5 entry arrived free from WordNet or from a v1.3
  payload.
* **$0.0009/entry** — the modelled cost of a full `generate` (COST-MODEL § 3: $0.86 per
  1,000 entries, at 3 senses). A monosemous name with the `overview` call skipped (§ 5,
  "the new path") should come in under this, so it is a ceiling.

| size | rows | already in store | **new entries** | WordNet-sourced (free import) | seeded `generate` | score floor | chain @ $0.003 | generate @ $0.0009 | **all-in** |
|---|---|---|---|---|---|---|---|---|---|
| 10,000 | 10,000 | 1,929 | 8,071 | 2,526 | 5,545 | 59.6 | $24 | $5 | **≈ $33** |
| **15,000** | 15,000 | 2,282 | **12,718** | 2,543 | 10,175 | 54.5 | $38 | $9 | **≈ $52** |
| 20,000 | 20,000 | 2,481 | 17,519 | 2,779 | 14,740 | 37.8 | $53 | $13 | **≈ $71** |

(All-in adds ~$1 for the alias pass and $3.40 for one Opus judge sample.)

**Recommendation: 15,000**, i.e. **12,718 new entries for ≈ $52 all-in** against caps of
about **$85** (1.5× the tier-2 per-field unit costs, the basis every previous tier used).

The reason is the score floor, not the money. At 10,000 the cut lands at 59.6 and stops
inside the level-4/level-5 body — it would leave out most US state capitals, most pro
sports teams and most of the second rank of American history. At 20,000 the floor falls to
37.8, which is where the list stops being vital-article-backed and becomes "any WordNet
instance lemma with a US boost" (*Battle of Fallen Timbers*, *Biola University*,
*Haverford College*) — good content, but a different and much flatter population. 15,000
lands at 54.5, which is one coherent band: a level-5 vital article with ~30 sitelinks, or
a WordNet name with a US signal. The extra 5,000 rows are a defensible **second batch**
for +$14 once the judge sample on the first is read.

### The chain

| # | stage | how | cost |
|---|---|---|---|
| 0 | **decide § 4d** (the settlement/polity domain leaf) and land § 4a-c: `entity_type` written rather than defaulted, `wikidata_qid` stored, `Lexeme.aliases` + `RelationType.ALIAS_OF` | code + a decision record | $0 |
| 1 | `import-wordnet --from-list data/core/tier6_candidates.tsv --source wordnet` | free, offline; covers the 3,880 rows WordNet has (2,543 of them new; D-78 skips the rest as already present), with gloss, examples, `instance_of` and the eight pointer types (D-78). The TSV's `source` column is `wordnet` on exactly those rows and `name_seed` on the rest, so this reads the right subset unchanged | $0 |
| 2 | **`generate --seed-list`** over the remaining 10,175 new rows — the new path, described below | luna, ≤ $0.0009/entry | ~$9 |
| 3 | `retrofit --only classify_kind`, `--only entity_type` (new), `--only tag_domain`, `--only spans` over 15,000 | nano | ~$5 |
| 4 | `resolve --all`, `graph-hygiene`, `sense-hygiene`, `relation-reconcile` | nano + free | ~$8 |
| 5 | **alias pass** over the 9,475 `alias_of candidate` rows: one nano call decides `alias_of` vs `see_also` vs nothing for each (name, store-single-word) pair, at `lexeme-hygiene`'s measured $0.00008/verdict (D-79) | nano | ~$1 |
| 6 | `enrich --fields gloss,examples,encyclopedia --reading-levels grade_1,grade_5,grade_10,college` over the 12,718 new entries. **No `--registers`** (§ 4f) | luna | ~$26 |
| 7 | `qa --sample 40 --seed 7` on the tier, `audit` | Opus | $3.40 |
| | **total** | | **≈ $52, caps ~$85** |

### The new `generate` path: seed from a name and a type

`generate` today starts with an `overview` call that asks the model what the headword *is*
— which parts of speech, how many senses, is it a proper noun, what entity type. For a
tier-6 row **every one of those answers is already known**, from a source better than a
model guess: the part of speech is `noun`, the sense count is 1, the kind is
`proper_noun`, the entity type and the QID came from Wikidata, and the hypernym came from
WordNet or from the Wikidata class. Paying for the overview call would be paying to
re-derive facts we already have, and would let the model overrule them.

So the proposal is a seeded entry point, not a new workflow:

* **`EntrySpec` gains four optional fields** — `kind: LexemeKind | None`,
  `entity_type: EntityType | None`, `wikidata_qid: str | None`, `hypernym: str | None`.
  Every one has a `None` default, so nothing existing changes (`EntrySpec` already
  documents that every field but `headword` has a usable default).
* **When `kind` and `entity_type` are both set, `generate_entry` skips the `overview`
  stage entirely** and synthesises the `DraftOverview` it would have produced: one
  `DraftPOSPlan(pos=noun, sense_count=1)`, and a `DraftProperNoun` built from the two
  given values. That removes one of the two content calls per entry, which is where the
  saving comes from — a seeded name should cost meaningfully less than $0.003.
* **The senses prompt gains a "what we already know" block**: the entity type in words,
  the hypernym, and the vital topic/section as a domain hint. This is the same trick D-17
  uses for `domain_hint` — a hint the model is free to write around, not a constraint it
  must satisfy.
* **`--seed-list <tsv>`** on the CLI reads the tier-6 TSV's `name`, `entity_type` and
  `qid` columns directly (`wordnet_import.read_candidates` is the model: header-driven,
  `source`-filtered) and drives the pool.
* **Nothing is asserted that the sources did not say.** Every row of the current 15,000
  carries a QID, so every one can be seeded; a future row without one falls back to
  today's unseeded `generate` unchanged.

This section is a design, not an implementation; building it is a separate change with
its own decision record.

### File and release plumbing

* `data/core/tier6_candidates.tsv` is gitignored like every other tier list; the checked-in
  preview is `docs/tier6_top200.md`.
* `export/hf_rows.py`'s `TIER_FILES` must gain `(TIER_TIER6, "tier6.tsv")` or the whole
  tier exports as `TIER_UNKNOWN` (D-75's rule: a word shared with an earlier list keeps
  the earlier tier, so ordering matters and tier 6 goes last).
* The builder is re-runnable and cache-backed:
  `uv run --with pyarrow python scripts/build_tier6_candidates.py --offline --size 15000`
  re-scores in ~5 s with no network (column-pruned parquet reads; the OS page cache does the rest). Dropping `--offline` on an empty cache re-fetches
  everything in about 25 minutes.

---

## 6. Open questions for the author

1. **§ 4d** — settlements and polities have no domain leaf. Add `nature.settlements` and
   `law_government.polities`, or accept `people_society.community_life`? 26% of the tier
   depends on the answer.
2. **§ 4c** — is `Lexeme.aliases` + `RelationType.ALIAS_OF` the right shape, or should an
   alias be a retired lexeme that redirects (the shape `lexeme-hygiene`'s fold already
   uses for inflections, D-79)? The fold precedent argues for the second.
3. **Size** — 15,000 now, or 10,000 now and the rest after the judge sample?
4. Should living public figures be excluded outright rather than penalised? 612 rows in
   the current 15,000 are living people.
