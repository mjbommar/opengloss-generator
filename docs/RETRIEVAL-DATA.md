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
