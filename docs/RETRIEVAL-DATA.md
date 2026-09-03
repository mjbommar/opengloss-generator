# Retrieval- and pretraining-data export formats

One section per feature in `docs/RETRIEVAL-DATA-PLAN.md`, appended by whichever agent
built it. Each section describes the output format and shows one real record produced
against `data/sample-300`. Append a new section for your feature; do not rewrite
another feature's section.

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
