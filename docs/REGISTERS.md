# Registers, styles, and reading levels — a citable taxonomy survey

Research date: 2026-09-02. Every value list below was fetched from a live source (not
recalled); URLs are given inline so this can be re-verified. Scope: this is a research
note evaluating `Register` and `ReadingLevel` in `src/opengloss_generator/schema.py`
(`schema.py:85-102`) and `docs/DESIGN.md` § 2.2. **No source code was changed.**

The short version: our current `Register` enum (`plain, informal, technical,
professional, marketing`) conflates three unrelated axes — formality, situational
register, and genre — and no single "LOC taxonomy" for register exists. What LOC
actually publishes is an *audience* code (MARC 008/22), not a register/style scale. The
closest thing to an "official, citable" register list is ISO 12620's terminology data
category `register` (`DC-423` in the current TBX Master Data Category List), which is
narrower than what dictionaries actually use.

## 1. ISO 12620 / TBX — the `register` data category

ISO 12620 (*Management of terminology resources — Data category specifications*) does
not itself enumerate values in its current edition — see § 1b. The concrete, machine-readable
value list lives in the **TBX Master Data Category List**, queried live today:

```
curl -s -A "Mozilla/5.0" -H "Accept: application/json" https://datcats.tbxinfo.net/datcats
```

This returned (2026-09-02) a data category:

| Field | Value |
|---|---|
| `identifier` | `register` |
| `pid` | `http://www.datcatinfo.net/datcat/DC-423` |
| `description` | "Classification indicating the level of language assigned to a term." |
| `classification` | `termNote` |

with the exact picklist (verbatim from the API response, `id` 62, values `id` 63–69):

| Picklist value |
|---|
| `colloquialRegister` |
| `neutralRegister` |
| `technicalRegister` |
| `in-houseRegister` |
| `bench-levelRegister` |
| `slangRegister` |
| `vulgarRegister` |

Source: [TBX DatCat API](https://datcats.tbxinfo.net/) (`GET /datcats`), item `DC-423`.

### 1a. The current TBX-Basic spec no longer uses `register` at all

**TBX-Basic Version 4 (2025)**, the current normative profile
(<https://www.terminorgs.net/downloads/TBX-Basic-V4.pdf>, fetched and converted with
`pdftotext`), does **not** include `register` among its 23 data categories. It was
replaced by two narrower categories:

- **§ 6.23 Usage status** (`DC-0070`, `<termNote type="usageStatus">`) — a 3-value
  picklist for *terminology governance*, not linguistic register: `preferred` (DC-0072),
  `admitted` (DC-0073), `deprecated` (DC-0074). "Used for controlled authoring and
  controlled translation purposes, to mark term usage preferences."
- **§ 6.9 Geographical usage** (`DC-0243`, `<termNote type="geographicalUsage">`) — free
  text (recommended as a picklist of ISO 3166 country codes or BCP 47 locale tags), for
  dialect/regional variation, not formality.

So the 7-value `register` picklist above is a *legacy* TBX/ISOcat data category still
resolvable via DatCatInfo's PID and still returned by the live DatCat API, but it is no
longer part of the profile most CAT/termbase tools validate against today. Cite it as
"ISO 12620 register data category (DC-423, TBX Master Data Category List)," not as
"current TBX-Basic."

### 1b. ISO 12620's own scope changed in 2019

Per the standard's own history (cross-checked via secondary summary of the ISO
record, since the standard text itself is paywalled at
<https://www.iso.org/standard/37243.html>): the 1999 and 2009 editions of ISO 12620
*were* a registry of data categories (values included). The **2019 edition** narrowed
scope to specifying *how* to build a data category registry, and is no longer itself a
registry — the registry function moved to DatCatInfo / the CLARIN Concept Registry. This
means "ISO 12620" is best cited as the *methodology* standard, and DC-423 in DatCatInfo
as the *value list* — they are two different documents now.

### 1c. Wider secondary literature cites an 11-value list

Older secondary sources (Wikipedia's historical text on ISO 12620, mirrored by
[HandWiki](https://handwiki.org/wiki/ISO_12620), and various NLP survey papers) describe
an 11-value ISO 12620 `register` list: **bench-level, dialect, facetious, formal, in
house, ironic, neutral, slang, taboo, technical, vulgar**. I could not independently
verify this exact 11-item list against a live primary document (the current Wikipedia
article has dropped this detail, and the ISOcat.org registry referenced by that older
text is no longer online). Treat the 7-value DatCatInfo API result in § 1 as the
verified authoritative list, and the 11-value list as a plausible superset attested only
in secondary sources.

## 2. TEI P5 `<usg>` — usage-label typology, not a fixed register vocabulary

TEI (Text Encoding Initiative) does not fix a value list either; it fixes the
*dimensions* along which a lexicographer may tag usage, leaving the values open.
Fetched from <https://www.tei-c.org/release/doc/tei-p5-doc/en/html/ref-usg.html>:

| `type` value | Meaning |
|---|---|
| `geo` | geographic area |
| `time` | temporal / historical era (archaic, old, etc.) |
| `dom` | domain or subject matter (e.g. scientific, literary) |
| `register` | register |
| `style` | style (figurative, literal, etc.) |
| `plev` | preference level (chiefly, usually, etc.) |
| `lang` | a language named in etymological/linguistic discussion |
| `gram` | grammatical usage |
| `syn`, `hyper`, `colloc`, `comp`, `obj`, `subj`, `verb` | collocational/paradigmatic usage aids |
| `hint` | unclassifiable sense-disambiguation hint |

This is the single most useful confirmation for this project: **TEI treats `register`,
`domain`, `time`-period, and `geo`-dialect as four separate attributes on the same
`<usg>` element**, not one merged field. That is the strongest standards-backed argument
for splitting our one `Register` enum into orthogonal axes rather than adding more
mixed values to it.

## 3. Library of Congress — MARC target audience, not register

LOC does not publish a register/style taxonomy for definitions. What it publishes is an
**audience** code, confirmed live:

### MARC 21 Bibliographic 008/22 — Target Audience (fetched via loc.gov mirror, `bd008b.html`, Books)

| Code | Label | Definition |
|---|---|---|
| `#` | Unknown or not specified | — |
| `a` | Preschool | intended for children, approximate ages 0–5 |
| `b` | Primary | intended for children, approximate ages 6–8 |
| `c` | Pre-adolescent | intended for young people, approximate ages 9–13 |
| `d` | Adolescent | intended for young people, approximate ages 14–17 |
| `e` | Adult | intended for adults |
| `f` | Specialized | aimed at a particular audience (e.g. technical material, limited appeal) |
| `g` | General | of general interest, not aimed at a particular target audience |
| `j` | Juvenile | intended for children and young people, approximate ages 0–15 |
| `\|` | No attempt to code | — |

Source: <https://www.loc.gov/marc/bibliographic/bd008.html> (canonical; mirror fetched at
`stuff.coffeecode.net/www.loc.gov/marc/bibliographic/bd008b.html` since loc.gov itself
returned 403 to the fetch tool). Also relevant, not fully verified live (fetch blocked,
403): **MARC 385 "Audience Characteristics"**, which lets a record point at controlled
terms from **LC Demographic Group Terms (LCDGT)** for the intended audience — LCDGT has
an "Age" category (children, adults, etc.) among nine categories (Age, Educational
Level, Ethnic/Cultural, Language, Medical/Psychological/Disability, National/Regional,
Occupation/Field of Activity, Religion, Social), per
<https://id.loc.gov/authorities/demographicTerms.html> and
<https://acrl.ala.org/anss/index.php/publications/cataloging-qa/what-are-the-library-of-congress-demographic-group-terms-and-how-are-they-used/>.
**LC Genre/Form Terms (LCGFT)** and LCDGT classify *what a work is* and *who it's about
or for*; neither encodes formality/register of the text's language. So "a taxonomy like
the LOC one" most plausibly means MARC 008/22 target-audience codes — an **audience**
axis, matching our `ReadingLevel`, not our `Register`.

## 4. Dictionary usage-label practice (OED / Oxford Languages, Wiktionary)

Dictionaries mix **formality**, **attitude/connotation**, **domain**, and
**time-period** labels under one informal "usage label" umbrella — which is precisely
the muddle TEI's `<usg>` keeps separate and our enum has fallen into.

**Oxford Languages** ("Labelling our datasets",
<https://languages.oup.com/about-us/labelling-our-datasets/>) documents (partial list,
confirmed by search-engine cache of the live page):

| Label | Definition (paraphrased from source) |
|---|---|
| Formal | found in official/legal documents |
| Informal | used with friends/family, not at work or in serious writing |
| Archaic | old-fashioned/historical, e.g. period films or religious texts |
| Dated | old-fashioned within the last ~100 years |
| Historical | describes things that existed in the past, no longer part of the modern world |
| Derogatory | deliberately critical or insulting |
| Euphemistic | a polite/indirect way of naming something unpleasant |
| Humorous | light-hearted or amusing |
| Figurative | non-literal, extended meaning |
| Dialect | restricted to a particular area, not standard English |

(The OED's own public glossary page, <https://www.oed.com/information/understanding-entries/symbols-and-other-conventions>,
documents symbols and abbreviations like `Obs.` and label conventions but a full
machine-readable label list was not directly fetchable — 403/soft-block on automated
fetch; the OUP list above is the same publisher's register vocabulary and is directly
fetchable.) Additional OED-specific labels found via search of OED-adjacent
documentation: **Colloquial**, **Literary**, **Technical**, **Old-fashioned** — each
explicitly said to "occur in combination" with others (an entry can be both `archaic`
and `literary`), confirming these are independent tags, not one enum position.

**Wiktionary** (<https://en.wiktionary.org/wiki/Appendix:List_of_language_registers>,
fetched live) has the broadest published register list of any source surveyed, 26
values — and explicitly organizes them as varying **situation/domain** (academic,
business, legal, medical, religious), **formality** (formal, informal/casual/colloquial,
frozen/static, consultative, intimate — this is Joos, see § 5), **social deixis**
(common, humble, polite, royal), and **attitude** (facetious, taboo, vulgar). The
Wiktionary `{{label}}` template
(<https://en.wiktionary.org/wiki/Template:label>) is the operational analogue of what
this project needs: one tag position that accepts values from many different
underlying "kinds" of label (register, domain, temporal, regional) without pretending
they're the same axis.

## 5. Linguistics: formality clocks vs. situational registers — two different theories

These are the two academic frameworks usually invoked, and they answer *different*
questions:

- **Joos (1961), *The Five Clocks*** — a pure **formality** scale, independent of topic
  or medium: **frozen → formal → consultative → casual → intimate**. Confirmed via
  Wikipedia's [Register (sociolinguistics)](https://en.wikipedia.org/wiki/Register_(sociolinguistics))
  article, which cites Joos, M. (1961), *The Five Clocks*, Harcourt, Brace and World, and
  glosses each level (frozen = "printed unchanging language... often archaisms";
  formal = "one-way participation... technical vocabulary or exact definitions
  important"; consultative = "two-way participation, prior knowledge not assumed";
  casual = "in-group, ellipsis and slang common"; intimate = "non-public, intonation
  more important than wording").
- **Biber & Conrad, *Register, Genre, and Style*** — defines **register** as a
  *situational* category (purpose, topic, participant relationship, mode, production
  circumstances), used to compare corpora like **conversation, fiction, news reportage,
  academic prose**. This is the theoretical grounding for what our enum calls
  `marketing`, `professional`, etc. — those are **situational/genre** categories, not
  formality levels. (Cambridge University Press abstract:
  <https://www.cambridge.org/core/books/abs/register-genre-and-style/describing-the-situational-characteristics-of-registers-and-genres/8ED307FFECA4339267D0D5536DC7C59E>;
  overview via <https://jan.ucc.nau.edu/biber/Biber/Biber_Conrad_2001.pdf>.) Note: an
  earlier, related framework — Halliday & Hasan's field/mode/tenor — covers similar
  ground and is what the Wikipedia register article actually expounds at length; Biber
  & Conrad's independent characteristics list is the more recent corpus-linguistics
  formulation of the same situational idea.

**This is the crux of the taxonomy problem**: Joos answers "how formal is the
language," Biber/Conrad answers "what kind of text is this." Our current enum's
`marketing` value is a Biber/Conrad-style genre label; `informal`/`plain` are
Joos-style formality labels; `technical`/`professional` are TEI/OED-style
domain-or-formality labels depending on how you read them. They are not comparable
values of one dimension.

## 6. Reading/audience level scales

| Scale | Granularity | Verified values | Source |
|---|---|---|---|
| **CEFR** (Council of Europe) | 6 bands, groupable into 3 | A1, A2 (Basic User); B1, B2 (Independent User); C1, C2 (Proficient User) | Search-confirmed against Council of Europe framework pages; official descriptor page (`coe.int/.../level-descriptions`) returned 403 to automated fetch, so cite the CEFR *document* itself (Council of Europe, *Common European Framework of Reference for Languages*, 2001/2020 Companion Volume) rather than a scraped page |
| **US school grade bands** | per-grade (K–12) | grade_1 ≈ ages 6–7; grade_5 ≈ ages 10–11; grade_10 ≈ ages 15–16; "college" ≈ 18+ | conventional US K-12 age norms |
| **Lexile** (MetaMetrics) | numeric (L) | e.g. 200L≈grade 1.5, 550L≈grade 3.0, 800L≈grade 5.0, 1150L≈grade 10.0, 1300L≈grade 13+ ("college and career ready") | <https://hub.lexile.com/lexile-grade-level-charts/> (secondary aggregator of MetaMetrics' published chart; MetaMetrics' own chart is behind a login-gated hub in places) |
| **MARC 008/22 target audience** | 8 codes + unknown/uncoded | see § 3 table | <https://www.loc.gov/marc/bibliographic/bd008.html> |

None of these four scales are natively compatible — CEFR measures *second-language*
proficiency, Lexile and US grade bands measure *native-reader* text complexity, and MARC
008/22 measures *publisher-declared intended audience* (a cataloging judgment, not a
readability metric). Any crosswalk between them (§ 8 below) is therefore necessarily
approximate, and I did not find an official cross-mapping table from any standards body
— the CEFR-to-age and CEFR-to-Lexile numbers found in search results are third-party
teaching-resource approximations, not primary-source equivalence tables. **MARC
008/22 is the most directly "citable, LOC-style" audience scale**, since it's the one
actual LOC/library-cataloging artifact in this space; CEFR is the most citable
*language-proficiency* scale; Lexile is the most citable *native-reader
text-complexity* scale.

## 7. Recommendation

### 7a. Split `Register` into two orthogonal axes

Grounded in TEI's `<usg>` split (§ 2) and Biber/Conrad vs. Joos (§ 5):

```python
class Formality(StrEnum):
    """Formality register, aligned to ISO 12620 / TBX DatCatInfo DC-423."""

    NEUTRAL = "neutral"        # DC-423 neutralRegister
    FORMAL = "formal"          # not in DC-423's 7, but ubiquitous in OED/Oxford/Wiktionary;
                                # needed because "professional" in our old enum was really this
    INFORMAL = "informal"      # DC-423 colloquialRegister (Oxford calls this "informal")
    TECHNICAL = "technical"    # DC-423 technicalRegister
    IN_HOUSE = "in_house"      # DC-423 in-houseRegister — jargon/internal shorthand
    SLANG = "slang"            # DC-423 slangRegister


class Genre(StrEnum):
    """Situational/communicative-purpose register, Biber & Conrad style.

    This is where "marketing" belongs — it is a text-type/purpose, not a formality
    level, and mixing it into Formality was the original schema.py bug this doc
    diagnoses.
    """

    GENERAL = "general"
    ENCYCLOPEDIC = "encyclopedic"
    MARKETING = "marketing"
    ACADEMIC = "academic"
```

If a second enum is more machinery than the project wants right now, the minimum fix
that stays a single enum but stops conflating axes is to **drop `marketing`** from
`Register` (move it to a `domain`/genre tag, or drop it — it doesn't have a citable
standards home as a *register*) and **rename `professional` to `formal`**, since
"professional" isn't a term any of the six sources use, while "formal" is used by
every one of them (ISO 12620 secondary list, TEI, OED/Oxford, Wiktionary, Joos).

### 7b. Keep `ReadingLevel` conceptually, but document its lineage

`ReadingLevel`'s `neutral, grade_1, grade_5, grade_10, college` is closest in spirit to
**US grade bands / Lexile**, not to MARC 008/22 (MARC's codes are *audience category*
labels like "preschool," not grade numbers) and not to CEFR (which is L2-proficiency,
irrelevant to a native-English-lexicon project). Recommendation: keep the enum as-is,
but document it explicitly as "US grade-band equivalent, informed by Lexile," and stop
implying a LOC/MARC connection — MARC is a *different* axis (intended audience
*category*, e.g. "juvenile" vs. "adult"), useful only as an illustrative crosswalk
below, not as a value source to rename into.

### 7c. Crosswalk table (approximate; no primary source unifies these)

| Our `ReadingLevel` | Approx. US grade | Approx. age | Approx. Lexile | Nearest MARC 008/22 | Nearest CEFR (L2, illustrative only) |
|---|---|---|---|---|---|
| `neutral` | n/a (register-neutral prose) | n/a | n/a | `g` General | n/a |
| `grade_1` | 1 | 6–7 | ~200L–300L | `a`/`b` Preschool/Primary | A1 |
| `grade_5` | 5 | 10–11 | ~750L–850L | `c` Pre-adolescent | A2/B1 |
| `grade_10` | 10 | 15–16 | ~1100L–1200L | `d` Adolescent | B2 |
| `college` | 13+ | 18+ | ~1300L+ | `e` Adult / `f` Specialized | C1/C2 |

## 8. Migration note for existing `Register` data

If `Register` is split or renamed, existing rendition data maps as follows:

| Current value | Maps to (§ 7a scheme) | Rationale |
|---|---|---|
| `plain` | `Formality.NEUTRAL` | matches DC-423 `neutralRegister`, Wiktionary "Neutral/Standard" |
| `informal` | `Formality.INFORMAL` | matches DC-423 `colloquialRegister`, OED/Oxford "informal"/"colloquial" |
| `technical` | `Formality.TECHNICAL` | matches DC-423 `technicalRegister` exactly — no change in meaning |
| `professional` | `Formality.FORMAL` | closest existing-standard analogue; "professional" itself appears in none of the six sources surveyed |
| `marketing` | `Genre.MARKETING` (not a `Formality` value) | it's a communicative-purpose/genre label (Biber & Conrad sense), not a formality level; no register standard surveyed treats "marketing" as a register |

If a two-enum split is judged too large a change right now, the smallest defensible
change grounded in this research is: rename `professional` → `formal`, and reclassify
`marketing` as a `domain`/genre concern rather than a register value — everything else
(`plain`→neutral naming aside) already lines up with ISO 12620/TBX DC-423 and dictionary
practice.

## Sources consulted (primary unless noted)

1. TBX Master Data Category List API — <https://datcats.tbxinfo.net/> (live JSON, `register` = DC-423)
2. TBX-Basic Version 4 (2025) — <https://www.terminorgs.net/downloads/TBX-Basic-V4.pdf>
3. TEI P5 `<usg>` element — <https://www.tei-c.org/release/doc/tei-p5-doc/en/html/ref-usg.html>
4. MARC 21 Bibliographic 008/22 Target Audience — <https://www.loc.gov/marc/bibliographic/bd008.html>
5. LC Demographic Group Terms — <https://id.loc.gov/authorities/demographicTerms.html>
6. Oxford Languages, "Labelling our datasets" — <https://languages.oup.com/about-us/labelling-our-datasets/>
7. Wiktionary, "Appendix:List of language registers" — <https://en.wiktionary.org/wiki/Appendix:List_of_language_registers>
8. Wikipedia, "Register (sociolinguistics)" (Joos citation) — <https://en.wikipedia.org/wiki/Register_(sociolinguistics)>
9. Biber & Conrad, *Register, Genre, and Style* (Cambridge) — <https://www.cambridge.org/core/books/abs/register-genre-and-style/describing-the-situational-characteristics-of-registers-and-genres/8ED307FFECA4339267D0D5536DC7C59E>
10. Lexile grade-level chart (secondary aggregator of MetaMetrics data) — <https://hub.lexile.com/lexile-grade-level-charts/>
