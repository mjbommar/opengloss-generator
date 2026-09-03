# Retrieval-data exports

Output formats for the retrieval- and pretraining-data features in
`docs/RETRIEVAL-DATA-PLAN.md`. Each feature appends its own section here, with one real
record pulled from a `data/sample-300` run (never `data/core-store`). See that plan for
the shared non-negotiables (schema v3 conventions, offline tests, D-nn numbering).

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
