# Retrieval- and pretraining-data outputs

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
