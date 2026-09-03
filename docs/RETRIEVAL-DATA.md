# Retrieval- and pretraining-data outputs

Companion to `docs/RETRIEVAL-DATA-PLAN.md` (the plan and the non-negotiables): this file
documents the *output format* of each shipped feature, one section per feature, appended
by whichever agent built it. Never rewritten wholesale — append your own section.

## F1 — `export-pairs`

`opengloss export-pairs --store S --out pairs.jsonl [--from-list L] [--easy-negatives N]
[--seed N]` (`src/opengloss_generator/export/pairs.py`). Free: no model call, reads
sense-tagged examples straight off the store. Only live senses of a non-retired entry
ever appear in a pair; a retired sense (or a retired entry) contributes nothing, on
either side.

### Record schema (one JSON object per line)

| Field | Type | Meaning |
|---|---|---|
| `headword` | string | The (first) headword the pair is about. |
| `headword_b` | string | Equal to `headword` except for `wic_easy_negative`, whose two texts come from different headwords. |
| `sense_a` / `sense_b` | string \| null | Derived sense id (`headword:pos:index`). `sense_b` is `null` for `example_encyclopedia`, since the encyclopedia is an entry-level rendition, not a sense. |
| `text_a` / `text_b` | string | The two texts being paired. |
| `span_a` / `span_b` | `[int, int]` \| null | The headword's character span within an example text; `null` for the gloss/encyclopedia side of a pair, and for either side when the source example has no recorded span. |
| `label` | 0 \| 1 | 1 for every positive pair kind; 0 for both negative kinds. |
| `level_a` / `level_b` | string | The `ReadingLevel` of each side's source rendition (e.g. `neutral`, `grade_5`). |
| `kind` | string | One of the five `PairKind` values below — this is the "negative kind" the plan's CLI summary is measured by. |

`headword`/`sense_a`/`sense_b`/`text_a`/`text_b`/`span_a`/`span_b`/`label`/`level_a`/
`level_b` are exactly the ten fields `RETRIEVAL-DATA-PLAN.md`'s F1 section names;
`headword_b` and `kind` are additive, needed to make an easy negative and a "what is
this pair for" distinction representable without a second file format.

### Pair kinds

* **`wic_positive`** (label 1) — every pairing (`itertools.combinations`, not just the
  canonical rendition) of one live sense's own example renditions. A sense with `k`
  example renditions contributes `C(k, 2)` pairs.
* **`wic_hard_negative`** (label 0) — one representative example from each pair of an
  entry's own live senses: same surface form, different meaning, the classic WiC case.
* **`wic_easy_negative`** (label 0, opt-in via `--easy-negatives N`) — one representative
  example from each of two live senses that share a domain leaf (`Sense.domain`) but
  belong to different headwords. The only sampled, seeded part of this export: each
  source sense draws up to `N` partners with its own `random.Random(f"{seed}:{domain}:
  {sense_id}")`, so the draw for one sense is independent of every other sense's draw
  and of iteration order. `N = 0` (the default) disables this kind entirely.
* **`example_gloss`** (label 1) — a sense's representative example paired with its own
  canonical (`neutral`, `plain`) gloss.
* **`example_encyclopedia`** (label 1) — a sense's representative example paired with
  its entry's canonical (`neutral`, `plain`) encyclopedia rendition, when the entry has
  written one; skipped otherwise.

"Representative example" is a sense's canonical (`neutral`, `plain`) example rendition
if it has one, else its first example in stored order — deterministic, never a random
pick.

### Determinism

Entries are processed in `lexeme_id` order regardless of store filesystem layout or
`--from-list` order, so `wic_positive`/`wic_hard_negative`/`example_gloss`/
`example_encyclopedia` pairs are byte-for-byte reproducible with no `--seed` involved at
all. `--seed` governs only `wic_easy_negative` sampling; a re-run with the same seed
over the same store reproduces the exact same file (verified: two runs of the
sample-300 command below diffed identical).

### One real record, from `data/sample-300`

Command run (free, no model, `data/core-store` never touched):

```
uv run opengloss export-pairs --store data/sample-300 --out pairs.jsonl \
    --easy-negatives 3 --seed 0
```

```json
{
  "headword": "abandoned",
  "headword_b": "abandoned",
  "sense_a": "abandoned:verb:0",
  "sense_b": "abandoned:verb:0",
  "text_a": "They abandoned the old car by the side of the road when it stopped working.",
  "text_b": "Students abandoned the hypothesis after the data contradicted it.",
  "span_a": [5, 14],
  "span_b": [9, 18],
  "label": 1,
  "level_a": "neutral",
  "level_b": "neutral",
  "kind": "wic_positive"
}
```

### Measured counts (`data/sample-300`, 300 entries, `--easy-negatives 3 --seed 0`)

| kind | count |
|---|---|
| `wic_positive` | 18,768 |
| `wic_hard_negative` | 1,836 |
| `wic_easy_negative` | 2,902 |
| `example_gloss` | 1,040 |
| `example_encyclopedia` | 1,040 |
| **total** | **25,586** |

By label: `1` (positive) = 20,848; `0` (negative) = 4,738. All 300 entries contributed
at least one pair. Without `--easy-negatives`, the same store yields 22,684 pairs (the
`wic_easy_negative` row drops to 0, everything else unchanged) — see D-54 in
`docs/DECISIONS.md` for both runs' full JSON summaries.

## F3 + F4 — `export-triples` and `export-qrels` (D-56)

Free: no model calls, no store writes. Both commands read a `LexemeStore` and share one
in-memory projection (`export/triples.py`'s `load_corpus`): every live sense's canonical
gloss, one example (if any), the owning lexeme's canonical encyclopedia entry (if any),
and the resolved `hypernym`/`hyponym`/`synonym`/`confusable_with` graph among live senses.
Deterministic for a fixed `--seed`: every random choice is made by a fresh
`random.Random` keyed on `f"{seed}:{sense_id}:{...}"`, never on dict/set iteration order,
so re-running with the same `(store, seed)` reproduces the file byte-for-byte.

### Where the query comes from

`Sense.queries` (F2) does not exist on `main` yet, so every query is read defensively
with `getattr(sense, "queries", [])`. When a sense has F2 queries, one row is emitted per
query, `query_id="<sense_id>#q<n>"`, `query_source="generated"`. When it has none — every
sense in the current store, since F2 hasn't landed — its `grade_5/plain` gloss rendition
stands in as a single pseudo-query (`query_id` is that rendition's id,
`"<sense_id>#grade_5/plain"`), or the canonical `neutral/plain` gloss when `grade_5` is
missing (`query_id="<sense_id>#neutral/plain"`); either way `query_source="gloss_pseudo"`.
Every triple and every listwise query records `query_source`, so a downstream trainer can
filter or reweight pseudo-queries once real F2 queries exist for the same senses.

### `export-triples`

```
uv run opengloss export-triples --store data/sample-300 --out triples.jsonl \
    --seed 0 --easy-negatives 1
```

One JSONL line per `(query, positive, negative)` triple:
`query, positive, negative, negative_kind, query_id, positive_id, negative_id, query_source`.

The positive is one of the sense's canonical gloss, one example, or its lexeme's
encyclopedia entry (chosen once per sense, seeded); `positive_id` is a sense id, a
`<sense_id>#example` id, or the lexeme's `<lexeme_id>:encyclopedia` id accordingly. The
negative is chosen by a **priority-ordered fallback**: the first non-empty candidate set,
in this order — another live sense of the same headword (`other_sense`), a
`confusable_with` target (`confusable`), a co-hyponym sharing a direct hypernym
(`co_hyponym`), or a synonym-of-a-synonym at graph distance 2 (`synonym_of_synonym`) —
supplies exactly one hard negative per query. A direct (distance-1) synonym is never
offered as a negative, since it is close enough to the query's own meaning that using it
as one would teach a false contrast. `--easy-negatives` (default 1) adds that many
additional rows per query whose negative is a random live sense from a different
headword (`negative_kind="easy"`). A negative is never the positive's own sense.

One real record (`abandoned:adjective:0`, a sense whose pseudo-query is its `grade_5`
gloss, from a `data/sample-300` run):

```json
{
  "query": "Empty because no one lives there or uses it now.",
  "positive": "Deserted or unoccupied; no longer inhabited or in use.",
  "negative": "Unrestrained or reckless; acting without restraint or thoughtful planning.",
  "negative_kind": "other_sense",
  "query_id": "abandoned:adjective:0#grade_5/plain",
  "positive_id": "abandoned:adjective:0",
  "negative_id": "abandoned:adjective:2",
  "query_source": "gloss_pseudo"
}
```

### `export-qrels`

```
uv run opengloss export-qrels --store data/sample-300 --out-dir qrels/ --seed 0
```

Writes three files under `--out-dir`, all keyed by the same `query_id` and document ids
as `export-triples`' `positive_id`/`negative_id` (documents are always a sense's
canonical gloss, id = sense id):

* `qrels.trec` — one `qid 0 docid grade` line per (query, candidate) pair (standard
  `trec_eval` qrels format).
* `docs.jsonl` — one `{"id", "text"}` line per document referenced anywhere in
  `qrels.trec`.
* `listwise.jsonl` — one `{"query", "query_id", "query_source", "candidates":
  [{"id", "text", "grade"}]}` line per query, the same information grouped for a
  listwise trainer that wants one query's whole ranked list at once. `query_source` is
  the extra field beyond the plan's minimal shape, needed because a store with mixed
  F2/pseudo queries otherwise has no way to tell the two apart from the listwise file
  alone.

Grades: **3** the query's own sense; **2** a direct synonym target (deliberately excluded
from `export-triples`' negatives — see above); **1** a direct hypernym or a co-hyponym;
**0** everything else, which includes every `export-triples` hard-negative kind
(`other_sense`, `confusable`, `synonym_of_synonym` — **not** `co_hyponym`, already spent
on grade 1) plus random easy negatives. Grade-2 and grade-1 candidate pools are capped at
3 each and, when larger, sampled deterministically; grade-0 contributes up to 3 as well
(one per non-empty hard-negative kind, padded with easy negatives). A sense is graded
against exactly one tier per query — the tiers are disjoint by construction
(`export.triples.classify`), and the easy-negative pad additionally excludes whatever a
higher tier already claimed, so no id is ever offered twice at two grades for one query.

One real record's full candidate list (`chronicle:noun:0`, which has a resolved direct
synonym in `data/sample-300`, plus its grade-0 tier: another sense of the same headword,
an easy negative, and — since the store's `confusable`/`co_hyponym` pools happened to be
empty for this sense — the padding reaching all the way to a second easy negative), from
`listwise.jsonl`:

```json
{
  "query": "A written record that tells about real events in the order they happened.",
  "query_id": "chronicle:noun:0#grade_5/plain",
  "query_source": "gloss_pseudo",
  "candidates": [
    {"id": "chronicle:noun:0", "text": "A factual written record of events arranged in the order they occurred.", "grade": 3},
    {"id": "timeline:noun:0", "text": "A written or drawn arrangement that lists historical events in the order they happened to show sequence and relationship.", "grade": 2},
    {"id": "chronicle:verb:0", "text": "To record in detail a sequence of events or facts in written form for historical or educational purposes.", "grade": 0},
    {"id": "backward:adjective:2", "text": "Not modern or progressive; oriented toward tradition and resisting change.", "grade": 0},
    {"id": "arbor:noun:0", "text": "In general academic usage, arbor refers to a garden structure consisting of a shaded, openwork arch or trellis covered with climbing plants, serving as a sheltered outdoor space in landscape design.", "grade": 0}

What each feature in `docs/RETRIEVAL-DATA-PLAN.md` writes, and what one real record of it
looks like. **Append your feature's section; do not rewrite anyone else's.**

## F5 — `contrasts`: an "X vs Y" paragraph per relation edge (D-57)

### What it is

For every `synonym`, `antonym` or `confusable_with` edge whose far end resolves to a sense
that is actually in the store, one paragraph of 60-120 words saying **how the two terms
differ** — the register that separates them, the axis they oppose on, the collocation that
picks one over the other — together with a `verdict` on whether they are related the way
the edge claims.

It is stored on the `Lexeme`, not on the sense, because a pair is written about once: when
both ends of a reciprocated edge are in the store, the end whose **sense id sorts smaller**
owns the pair and the far end defers. An edge the far side does not reciprocate is owned by
whichever end asserts it.

### Where it lives

`Lexeme.contrasts`, a list of `Contrast` (schema v3 § 9, D-62):

```json
{
  "edge_id": "mail:noun:0-synonym->post",
  "target_sense_id": "post:noun:0",
  "verdict": "related_as_typed",
  "provenance_id": "p8",
  "text": [
    {
      "reading_level": "neutral",
      "register": "plain",
      "content": "Mail and post can both refer to letters and parcels handled by the postal system, but the usual choice depends mainly on variety of English. ..."
    }
  ]
}
```

### Measured on `data/sample-300` (300 entries, seed 0)

|  | entries scanned | live senses / queries | rows written |
|---|---|---|---|
| `export-triples` | 300 | 1,041 | 2,038 triples |
| `export-qrels` | 300 | 1,041 | 4,269 qrels rows / 1,041 docs / 1,041 listwise queries |

`export-triples` negative-kind histogram: `other_sense` 997, `easy` 1,041 (`confusable`,
`co_hyponym`, `synonym_of_synonym` did not fire — see the D-56 note on why a 300-entry
sample under-represents the three graph tiers whose targets usually live in a different
sampled entry).

`export-qrels` grade histogram: grade 3 1,041; grade 2 48; grade 1 57; grade 0 3,123.

Every `query_source` in this run is `gloss_pseudo`, since F2 (`Sense.queries`) has not
landed on `main` yet.

## F9 — `export-pretrain`

`opengloss export-pretrain --store S --out docs.jsonl [--templates T] [--levels L]
[--per-entry N] [--seed S] [--from-list L]` serialises each entry into up to four plain
prose/light-markdown documents — a dictionary entry, a thesaurus entry, an encyclopedia
article, and a usage note — with no JSON/YAML duplication and no special tokens. It
reads only fields the store already has (glosses, examples, relations, encyclopedia,
etymology, lexical explanation, register variants, and F5's contrasts when present); it
makes no model calls and never writes to the store.

Retired senses never contribute to any template. A section with nothing to say (no
relations of the four thesaurus kinds, no register variant ever written, no
etymology/encyclopedia/explanation at all) is left out of its document entirely rather
than emitted empty; a template with nothing at all to say for an entry is skipped for
that entry. `--levels` selects which reading levels get their own document; a section
that has no rendition at the requested level falls back to the canonical
`(neutral, plain)` text, and the whole document's `level_used` is reported as
`"neutral"` when any part of it needed that fallback. `--per-entry N` (with `--seed`)
caps how many of the available templates one entry gets, drawn deterministically from
`seed` and the entry's own id, so a corpus built with a small `N` still mixes templates
across entries rather than always keeping the same ones.

Output is one JSON object per line:

```json
{"id": "everywhere#pretrain-dictionary-neutral", "headword": "everywhere", "template": "dictionary", "level": "neutral", "level_used": "neutral", "text": "...", "n_words": 28}
```

`id` is derived (`<lexeme_id>#pretrain-<template>-<level>`), never randomly assigned, so
it can be recomputed from the JSONL alone. Document order is `entries` (by `lexeme_id`,
independent of on-disk shard layout) × chosen templates (fixed order: dictionary,
thesaurus, encyclopedia, usage_note) × requested levels — deterministic across runs and
machines for the same inputs.

### One real document per template (`data/sample-300`, `--levels neutral`)

**Dictionary** (`everywhere#pretrain-dictionary-neutral`, 28 words):

```
# everywhere
## Adverb
1. In all places; distributed across every location.
   - "The species is found everywhere within the reserve."
   - "There were muddy footprints everywhere after the kids came in."
```

**Thesaurus** (`everywhere#pretrain-thesaurus-neutral`, 26 words):

```
# everywhere
## Adverb sense 1: In all places; distributed across every location.
Synonyms: universally, ubiquitously, everyplace.
Antonyms: nowhere, locally.
See also: worldwide, adverb, grammatical category, anywhere, everyplace.
```

**Usage note** (`lethal#pretrain-usage_note-neutral`, 55 words):

```
# lethal
## Adjective sense 1: Capable of causing death; sufficient to end life.
Informally: Able to kill someone or end a life.; In formal writing: Having the capacity to cause death or terminate life.; In technical writing: Having sufficient effect to produce death in a living organism.; In marketing copy: Powerful enough to end a life..
```

**Encyclopedia** (`indoor#pretrain-encyclopedia-neutral`, 512 words — the shortest
encyclopedia document in the sample; the template's `## Overview`/`## Etymology`/
`## Why This Word` sections are inherently longer prose than the other three
templates):

```
# indoor
## Overview
Indoor is an English adjective designating phenomena that occur within an enclosed structure, typically a human-made building. In general academic discourse, the term contrasts with outdoor, marking a fundamental environmental distinction relevant to disciplines such as architecture, environmental science, public health, sports science, and sociology. Indoor conditions are often characterized by controlled or semi-controlled variables, including temperature, humidity, air quality, lighting, and acoustics, which differentiate them from the more variable conditions of open-air environments.

In architecture and building science, indoor commonly modifies nouns like environment, air quality, navigation, and space usage. The concept of indoor environmental quality (IEQ) integrates thermal comfort, ventilation, lighting, and acoustical performance, and is central to green building standards and occupational health research. In environmental and health studies, indoor air pollution—originating from combustion, building materials, and household products—constitutes a major area of risk assessment, particularly in densely populated or poorly ventilated dwellings.

Across the social and behavioral sciences, the indoor/outdoor distinction structures analyses of human behavior, risk exposure, and spatial practices. For example, indoor versus outdoor physical activity is associated with different patterns of social interaction, safety, and accessibility. In sports and recreation, indoor specifies facilities (e.g., indoor arenas or indoor courts) and variants of sports whose rules or equipment are adapted to confined, climate-controlled spaces.
## Etymology
English *indoor* is a relatively recent compound formed in the late 18th to early 19th century from the preposition *in* and the noun *door*, originally in the sense "within doors, inside a building," with later adjectival use describing activities or spaces situated within an enclosed structure. In English, it appeared as "indoor (adjective)" (meaning "situated, existing, or carried on within doors; inside a building"), during the late 18th-early 19th c.. In English, it appeared as "in (preposition/adverb)" (meaning "inside, within"), during the Old English (before 12th c.). In English, it appeared as "door (noun)" (meaning "movable barrier used to close an opening; doorway, entrance"), during the Old English (before 12th c.). Cognates include indors (Scots, historical form meaning 'indoors'), indoors (English adverb), Binnenraum (German, 'indoor space', semantically related, not cognate).
## Why This Word
Indoor is an adjective describing something situated, occurring, or used within a building or other covered structure rather than outside. It commonly refers to activities, spaces, equipment, or conditions designed for use under shelter, such as indoor lighting, indoor plants, or indoor sports. The word often implies protection from weather, controlled surroundings, and limited exposure to outdoor elements. Synonyms include inside, interior, enclosed, and enclosed-space, which emphasize being within boundaries or under cover. Antonyms are outdoor, outside, natural, and all-weather, referring to what is exposed to the external environment or intended for open-air use. As a broader term, indoor belongs under adjectives describing location, environment, or setting, especially those related to enclosed or internal spaces. Narrower forms include indoor sports, indoor recreation, indoor-safe, and indoor-friendly, which specify particular uses or suitability for indoor conditions. The term is widely used in everyday, technical, and commercial contexts to distinguish interior from exterior settings.
```

### Measured on `data/sample-300` (D-61)

300 entries, all four templates, `--levels neutral,grade_5,college`: 3,600 documents,
1,122,835 words. See D-61 in `docs/DECISIONS.md` for the full per-template/per-level
breakdown and the caveat about `data/sample-300` being a fixture shared (and
concurrently written to) by other agents' worktrees during this feature's build.

`edge_id` is the derived id from `Lexeme.edges()` and is the contrast's identity: one
contrast per edge, enforced by a validator. `text` is a `Renditions[str]`, so the paragraph
can be levelled later like any other prose; the canonical `(neutral, plain)` rendition is
required and is the only one this stage writes. `provenance_id` points at the call that
paid for it, whose `note` is also this workflow's D-47 sentinel.

### Two real records from the pilot

Both are verbatim from the 48 paragraphs written over `data/sample-300` on 2026-09-03.

**A synonym** — `mail:noun:0-synonym->post`, verdict `related_as_typed`:

> Mail and post can both refer to letters and parcels handled by the postal system, but the
> usual choice depends mainly on variety of English. Mail is the normal term in American
> English, while post is especially common in British English and several other varieties.
> In British usage, the post can also mean the postal service or its delivery, as in "the
> post arrived," whereas mail more often names the items collectively. In either variety, a
> letter can be described as mail or post, so choosing the less usual regional term may
> sound foreign rather than change the basic meaning.

**An antonym** — `entry:noun:0-antonym->escape`, verdict `related_as_typed`:

> Entry and escape form an opposition on the axis of movement through an opening: entry is
> for going in, while escape is for getting out or being released. The contrast is clearest
> when the same structure serves both purposes, as with a building's entry and its emergency
> escape route. Entry is the unmarked term for an ordinary access point; escape usually
> signals a special purpose, such as danger, emergency release, or preventing confinement.
> The opposition applies to these physical senses, but not to every meaning of the words:
> entry can also mean a written record, and escape can mean avoiding capture.

### The verdict

`related_as_typed` | `related_differently` | `unrelated`. It is **recorded and counted, and
nothing else**: relation hygiene owns relation edits (D-50). The sweep summary carries the
histogram so a human, or a later `relation-hygiene` run, can go and look. On the pilot the
`related_differently` verdicts were mostly right and mostly the same defect — a relation
resolved to a sense of the wrong part of speech (`post` the verb pointed at `mail` the noun;
`poorly` the adverb pointed at `healthy` the adjective) — which makes the verdict a useful
free by-product of buying the prose.

### Command

```bash
opengloss contrasts --store data/sample-300 --budget 0.50 --concurrency 8
opengloss contrasts --store data/sample-300 --only-kinds synonym,antonym --dry-run
```

`--from-list` restricts the sweep to a word list; without it, every entry in the store is
visited. `--dry-run` counts the pairs exactly and prices them from the measured per-call
means, making no model call.

### Downstream

F3's `export-triples` mines these same edges for hard negatives. Where a contrast exists it
is the human-readable statement of *why* the negative is a negative, which is what makes a
mined triple auditable; F9's `export-pretrain` uses contrasts as the body of its usage-note
template.

What the features in `docs/RETRIEVAL-DATA-PLAN.md` write, in the shape a consumer
(`../opengloss-embedding`) actually reads. One section per feature, appended as each one
lands — **append, do not rewrite**. Every record shown here is real: copied out of a store
after a real run, not composed for the document.

## F2 — `queries`: synthetic retrieval queries per sense (D-55)

### Where it is stored

Inline on the sense, as `Sense.queries` (schema v3 § 9, D-62). Nothing is written to a
side file: a query belongs to the meaning it retrieves, and an exporter that wants
`(query, positive)` pairs reads it off the entry it was already reading.

```json
{
  "text": "Where can I get cash without going inside a bank?",
  "style": "question",
  "provenance_id": "p29"
}
```

Ids are derived, never stored: `identity.query_id(sense_id, index)` gives
`atm:noun:0#q1` for the second query on that sense. They are **positional and
zero-based**, so the stage only ever appends. `provenance_id` points at the call that
wrote it, in the entry's own provenance table:

```json
"p29": {
  "stage": "queries",
  "model": "gpt-5.6-luna",
  "prompt_version": "7",
  "service_tier": "flex",
  "input_tokens": 2223,
  "cached_input_tokens": 2056,
  "output_tokens": 281,
  "cost_usd": 0.00020586,
  "attempts": 1,
  "note": "queries:atm:noun:0:a7b0a521a151fae0;attempts=1",
  "run_id": "20260903T094559Z-8669fee3"
}
```

The `note` is the D-47 idempotence marker: the stage's prefix, the sense it answered for,
a digest of that sense's canonical gloss and the run's `per_sense`, and the attempt
number. A rerun over an unchanged gloss reads that note and costs nothing.

### One real sense, all twelve queries

`atm`, noun sense 0 — *"A self-service financial terminal that dispenses cash and enables
basic account transactions."* Its two siblings, which the queries have to discriminate
against, are the pressure unit (*"A unit of pressure equal to 101325 pascals…"*) and the
adjective (*"Relating to the atmosphere or to atmospheric conditions…"*). Written by the
default policy (`gpt-5.6-luna`, flex, `low` effort) on 2026-09-03:

| # | style | text |
|---|---|---|
| q0 | `keyword` | cash withdrawal machine |
| q1 | `question` | Where can I get cash without going inside a bank? |
| q2 | `conversational` | I need cash for a taxi but the branch is closed |
| q3 | `constraint` | find a cash machine that accepts my card and works after hours |
| q4 | `role` | for a traveler needing local currency and a balance check |
| q5 | `example_based` | show me a sentence using ATM for a cash machine |
| q6 | `step_by_step` | walk me through withdrawing cash and checking my balance |
| q7 | `directive` | compare a cash machine with a bank teller |
| q8 | `keyword` | machine for cash withdrawals and account access |
| q9 | `question` | Can an ATM let me check my account balance? |
| q10 | `conversational` | I only need to take out money, not speak to a cashier |
| q11 | `directive` | explain what basic transactions I can do at an ATM |

Three of the twelve name the headword; nine describe the meaning from the outside, which
is the property the stage exists for — a query set that all named its own headword would
be solved by a keyword index and would teach an encoder to match a string. All eight
styles appear, the four slack slots going to the styles this sense actually supports more
than one of. Not one of the twelve would be answered by the pressure unit or the
adjective.

### The consumer's view

A `(query, positive)` training pair is any query on a live sense against that sense's own
canonical gloss, any of its examples, or the entry's encyclopedia text. The hard negative
is the *same entry's other senses* — which is why the discrimination rule above is the
part of the prompt that matters, and why F3/F4 (`export-triples`, `export-qrels`) can
build MS MARCO triples and graded qrels out of this with no further model calls.

### Free measurements the run reports

`opengloss queries` prints these, and they are the numbers the stage is judged on rather
than an eyeball:

* `headword_free_share` — the share of stored queries containing no form of their own
  headword. The instruction asks for ≥ 0.5; the default model measured **0.782** over 218
  senses.
* `senses_below_headword_free_target` — senses that missed that bar (5 of 218).
* `senses_with_full_style_coverage` — senses whose stored set spans all eight styles
  (216 of 218).
* `stored_by_style` — the histogram, so a style silently collapsing is visible.
* `rejected_by_reason` — `duplicate`, `too_long`, `empty`, `surplus`.

### Running it

```bash
opengloss queries --store data/sample-300 --from-list words.txt \
    --budget 0.50 --concurrency 8 [--per-sense 12] [--dry-run]
```

`--dry-run` reads every entry's markers and prints the exact count of senses due plus a
priced estimate; it makes no model calls. One ledger record per sense is written to
`runs/<run_id>.ledger.jsonl`, carrying that call's cost and token counts.
