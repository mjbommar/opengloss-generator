# Retrieval- and pretraining-data outputs

What each of the features in `docs/RETRIEVAL-DATA-PLAN.md` actually produces, with one
real record per feature so a consumer can see the shape before writing a loader. Each
feature owns its own section and **appends** it; nothing here is rewritten by a later
agent.

---

## F6 — `qa-pairs`: grounded question/answer pairs (D-58)

### What it is

One `gpt-5.6-luna` call per live sense buys seven `QAPair` rows — one of each
`QuestionType` — at mixed `Difficulty`, answered **only** from text the store already
holds for that sense. The prompt carries four kinds of source, each labelled with the id
the answer has to cite:

| Source | Id form | Example |
|---|---|---|
| canonical gloss | `<sense_id>#neutral/plain` (a real `rendition_id`) | `projection:noun:1#neutral/plain` |
| example sentences (≤ 6, de-duplicated) | `<sense_id>#ex<n>` — stage-local, see below | `projection:noun:1#ex3` |
| encyclopedia passage (neutral/plain, ≤ 500 words) | `<lexeme_id>:encyclopedia#neutral/plain` | `projection:encyclopedia#neutral/plain` |
| etymology summary | `<lexeme_id>:etymology` — stage-local | `projection:etymology` |

Two of those four id forms are **not** derived in `identity.py`, deliberately.
`Lexeme.rendition_ids()` documents that example renditions have no unique derived id
(several may share one `(reading level, register)` key), and a sense's `(neutral, plain)`
example would collide with its `(neutral, plain)` gloss under `rendition_id` — exactly the
ambiguity a citation must not have. Etymology is not a rendition set at all. Both forms
therefore live next to their only consumer, in `workflows/qa_pairs.py`, and are candidates
for promotion into `identity.py` when F1/F3's exports become the second consumer.

### Storage

`Sense.qa: list[QAPair]`, positional ids `<sense_id>#qa<n>` (`Sense.qa_ids`). A sense
rejects two pairs with the same normalised question (`Sense._questions_are_distinct`).
Every stored pair carries the `provenance_id` of the call that wrote it, and that same
record carries the D-47 marker `qa_pairs:<sense_id>:<digest>;attempts=<n>`, so a rerun over
an unchanged sense costs $0 and a sense whose gloss or sources changed earns one more call,
two per sense in all.

### The three free post-checks

A drafted pair is dropped, and counted, when:

* `grounded_in` is empty (`no_citation`), or names an id that was not supplied
  (`unknown_citation`);
* its answer shares fewer than **two** content words — lowercase alphabetic runs of three
  or more characters, minus a small closed-class stopword list — with the text of the
  renditions it cited (`not_grounded`);
* its normalised question repeats one accepted earlier in the same answer, or one the
  sense already holds (`duplicate_question`).

### One real record

`projection:noun:1`, from the sample-300 pilot. Gloss: *"The act of displaying an image or
video by casting light onto a surface."* All seven pairs, verbatim:

```json
[
 {"question_type": "definition", "difficulty": "easy",
  "question": "What does projection mean when an image or video is displayed on a surface?",
  "answer": "Projection is the act of displaying an image or video by casting light onto a surface.",
  "grounded_in": ["projection:noun:1#neutral/plain"]},

 {"question_type": "factual", "difficulty": "easy",
  "question": "What did the teacher display on the wall using projection?",
  "answer": "The teacher used projection to display a map on the wall.",
  "grounded_in": ["projection:noun:1#ex0"]},

 {"question_type": "procedural", "difficulty": "easy",
  "question": "How did projection change the empty warehouse wall?",
  "answer": "It transformed the wall into a changing display of architectural plans.",
  "grounded_in": ["projection:noun:1#ex5"]},

 {"question_type": "comparison", "difficulty": "medium",
  "question": "How does projecting an image onto a surface differ from a map projection?",
  "answer": "Projecting an image onto a surface casts light to display the image, while a map projection translates Earth's curved surface to a flat map and inevitably distorts some properties.",
  "grounded_in": ["projection:noun:1#neutral/plain", "projection:encyclopedia#neutral/plain"]},

 {"question_type": "causal", "difficulty": "medium",
  "question": "Why can a map projection distort some properties?",
  "answer": "It can distort them because it translates Earth's curved surface to a flat map.",
  "grounded_in": ["projection:encyclopedia#neutral/plain"]},

 {"question_type": "hypothetical", "difficulty": "easy",
  "question": "If projection is used during a school play, what can it place behind the actors?",
  "answer": "It can put a forest scene behind the actors.",
  "grounded_in": ["projection:noun:1#ex3"]},

 {"question_type": "reasoning", "difficulty": "hard",
  "question": "A geometric projection maps each point to a closest point on a line or plane, while image projection casts light onto a surface. What common feature do these two uses share?",
  "answer": "Both involve directing or transferring something toward a specified spatial target: a point is mapped to a line or plane in geometry, while light carries an image to a surface.",
  "grounded_in": ["projection:encyclopedia#neutral/plain", "projection:noun:1#neutral/plain"]}
]
```

Every one of those citations resolves: `#ex0` is *"The teacher used projection to display
the map on the wall."*, `#ex3` is *"During the school play, projection put a forest scene
behind the actors."*, `#ex5` is *"Projection transformed the empty warehouse wall into a
changing display of architectural plans."*, and the two encyclopedia claims are both
sentences of the stored passage. The `reasoning` pair is what the stage exists for: it
combines the entry-level passage's *geometric* projection with this sense's *optical* one
into a statement neither source makes on its own, and cites both.

### Measured cost (sample-300 pilot, 2026-09-03)

From `runs/20260903T093145Z-0866e86d.ledger.jsonl`, 1,034 calls, plus a 7-call smoke run:

| | |
|---|---|
| calls / senses written | 1,034 |
| cost | **$0.426915** (1,041 senses over both runs: $0.430123) |
| cost per sense | **$0.000413** |
| cost per accepted pair | **$0.000060** |
| output tokens per call | mean **510**, median 496, p90 585, max 959 |
| input tokens per call | mean 2,874, of which 2,006 cached (70% hit rate) |
| pairs generated / accepted | 7,238 / 7,121 (**98.4%**) |
| drops | `not_grounded` 100, `unknown_citation` 17 |
| full 7-type coverage | 896 of 1,034 senses (86.7%) |
| difficulty mix | easy 3,084 / medium 2,974 / hard 1,063 |
| wall clock | 17.8 min at concurrency 16 |

No number here is projected past the pilot.

### Reading it back

```python
from opengloss_generator.store import LexemeStore

for lexeme_id in store.iter_ids():
    entry = store.read(lexeme_id)
    for _, sense, sense_id in entry.iter_senses():
        for pair_id, pair in zip(sense.qa_ids(sense_id), sense.qa, strict=True):
            ...  # pair.question, pair.answer, pair.question_type, pair.grounded_in
```

`grounded_in` is not validated against `Lexeme.rendition_ids()` at load time (D-62): a
re-levelled rendition would otherwise make an entry refuse to load. A consumer that needs
the cited *text* should resolve ids leniently and skip what it cannot find.
