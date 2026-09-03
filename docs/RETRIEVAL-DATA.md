# Retrieval- and pretraining-data outputs

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
