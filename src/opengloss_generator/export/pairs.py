"""F1: free WiC-style and positive pairs, mined from sense-tagged examples.

Why this is free money: every sense already carries example renditions written
*against that sense specifically* (D-53), a gloss, and (per entry) an encyclopedia
article. That is exactly the supervision a WiC (word-in-context) task or a doc2query
positive pair needs, and an embedding/reranker model (the target consumer,
``../opengloss-embedding``) is normally trained on such pairs only after someone pays an
annotator or another model to produce them. No model call happens here; every pair is
read straight off the store.

Five kinds of pair come out of one entry (``PairKind``):

* ``wic_positive`` -- two example renditions of the *same* live sense (label 1). A sense
  legitimately holds several example renditions (different reading levels/registers,
  and Schema v3 allows more than one canonical example), so this is *every* pairing of
  them, not just the canonical ones.
* ``wic_hard_negative`` -- one representative example from each of two *different* live
  senses of the *same* headword (label 0). This is the genuinely hard WiC case: same
  surface form, different meaning.
* ``wic_easy_negative`` -- one representative example from each of two live senses that
  share a domain leaf but belong to *different* headwords (label 0). Optional
  (``--easy-negatives N``), and the only source of randomness in this module: sampled
  with a seeded, per-source-sense RNG so the draw is independent of iteration order.
* ``example_gloss`` -- a sense's representative example paired with its own canonical
  (neutral, plain) gloss (label 1): the doc2query-shaped "this text should retrieve this
  definition" pair.
* ``example_encyclopedia`` -- a sense's representative example paired with its entry's
  canonical encyclopedia rendition, when one exists (label 1): same idea, aimed at the
  longer article instead of the one-line gloss.

"Representative example" (:func:`_representative_example`) is the sense's canonical
(neutral, plain) example if it has one, else its first example in stored order --
deterministic, and cheap to explain to a downstream consumer.

Retired senses, and entries whose own ``status`` is retired, contribute no pair at all,
on either side of any pair (the plan's non-negotiable for this feature). Output order is
fully determined by entry and sense ids, never by filesystem iteration order; the only
knob that needs a ``--seed`` is easy-negative sampling.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from opengloss_generator.schema import EntryStatus, Example, Rendition, Renditions

if TYPE_CHECKING:
    from opengloss_generator.schema import Lexeme, Sense
    from opengloss_generator.store import LexemeStore

__all__ = ["ExportPairsOutcome", "Pair", "PairKind", "export_pairs"]


class PairKind(StrEnum):
    """What a :class:`Pair` was mined from, i.e. the "negative kind" the plan asks for."""

    WIC_POSITIVE = "wic_positive"
    WIC_HARD_NEGATIVE = "wic_hard_negative"
    WIC_EASY_NEGATIVE = "wic_easy_negative"
    EXAMPLE_GLOSS = "example_gloss"
    EXAMPLE_ENCYCLOPEDIA = "example_encyclopedia"


@dataclass(frozen=True, slots=True)
class Pair:
    """One training pair, serialised as one JSONL record.

    ``headword``/``headword_b`` differ only for a ``wic_easy_negative`` pair; every
    other kind pairs two texts from the same entry. ``sense_b`` is ``None`` for
    ``example_encyclopedia`` pairs, since the encyclopedia is an entry-level rendition
    with no owning sense. ``span_a``/``span_b`` are the headword span within an example
    text (``None`` for the gloss/encyclopedia side of a pair, which is prose, not a
    tagged sentence).
    """

    headword: str
    headword_b: str
    sense_a: str | None
    sense_b: str | None
    text_a: str
    text_b: str
    span_a: tuple[int, int] | None
    span_b: tuple[int, int] | None
    label: int
    level_a: str
    level_b: str
    kind: str


@dataclass(slots=True)
class ExportPairsOutcome:
    """Counts backing the run summary the CLI prints."""

    entries_scanned: int = 0
    entries_with_pairs: int = 0
    pairs_written: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)

    def record(self, pair: Pair) -> None:
        """Fold one written pair into the label/kind counters."""
        self.pairs_written += 1
        label_key = str(pair.label)
        self.by_label[label_key] = self.by_label.get(label_key, 0) + 1
        self.by_kind[pair.kind] = self.by_kind.get(pair.kind, 0) + 1

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view, kept in fixed key order across runs."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_with_pairs": self.entries_with_pairs,
            "pairs_written": self.pairs_written,
            "by_label": dict(sorted(self.by_label.items())),
            "by_kind": dict(sorted(self.by_kind.items())),
        }


def _representative_example(renditions: Renditions[Example]) -> Rendition[Example] | None:
    """Return a sense's canonical example, or its first stored example, or ``None``.

    Deterministic: the canonical (neutral, plain) rendition wins when present; otherwise
    the first example in stored (insertion) order, never a random pick.
    """
    canonical = renditions.canonical()
    if canonical is not None:
        return canonical
    return renditions[0] if len(renditions) else None


def _live_senses(entry: Lexeme) -> list[tuple[Sense, str]]:
    """Return ``(sense, sense_id)`` for every non-retired sense of a non-retired entry."""
    if entry.status is EntryStatus.RETIRED:
        return []
    return [(sense, sid) for _, sense, sid in entry.iter_senses() if not sense.retired]


def _wic_positive_pairs(entry: Lexeme, live: Sequence[tuple[Sense, str]]) -> Iterator[Pair]:
    """Yield every same-sense pair among one sense's own example renditions."""
    for sense, sid in live:
        examples = list(sense.examples)
        if len(examples) < 2:  # noqa: PLR2004 - "a pair" needs at least two
            continue
        for a, b in itertools.combinations(examples, 2):
            yield Pair(
                headword=entry.headword,
                headword_b=entry.headword,
                sense_a=sid,
                sense_b=sid,
                text_a=a.content.text,
                text_b=b.content.text,
                span_a=a.content.span,
                span_b=b.content.span,
                label=1,
                level_a=a.reading_level.value,
                level_b=b.reading_level.value,
                kind=PairKind.WIC_POSITIVE.value,
            )


def _wic_hard_negative_pairs(entry: Lexeme, live: Sequence[tuple[Sense, str]]) -> Iterator[Pair]:
    """Yield one pair per pair of the entry's own live senses (the WiC hard case)."""
    reps = [(sid, _representative_example(sense.examples)) for sense, sid in live]
    usable = [(sid, rend) for sid, rend in reps if rend is not None]
    for (sid_a, rend_a), (sid_b, rend_b) in itertools.combinations(usable, 2):
        yield Pair(
            headword=entry.headword,
            headword_b=entry.headword,
            sense_a=sid_a,
            sense_b=sid_b,
            text_a=rend_a.content.text,
            text_b=rend_b.content.text,
            span_a=rend_a.content.span,
            span_b=rend_b.content.span,
            label=0,
            level_a=rend_a.reading_level.value,
            level_b=rend_b.reading_level.value,
            kind=PairKind.WIC_HARD_NEGATIVE.value,
        )


def _gloss_pairs(entry: Lexeme, live: Sequence[tuple[Sense, str]]) -> Iterator[Pair]:
    """Yield one (representative example -> canonical gloss) pair per live sense."""
    for sense, sid in live:
        rep = _representative_example(sense.examples)
        gloss = sense.gloss.canonical()
        if rep is None or gloss is None:
            continue
        yield Pair(
            headword=entry.headword,
            headword_b=entry.headword,
            sense_a=sid,
            sense_b=sid,
            text_a=rep.content.text,
            text_b=gloss.content,
            span_a=rep.content.span,
            span_b=None,
            label=1,
            level_a=rep.reading_level.value,
            level_b=gloss.reading_level.value,
            kind=PairKind.EXAMPLE_GLOSS.value,
        )


def _encyclopedia_pairs(entry: Lexeme, live: Sequence[tuple[Sense, str]]) -> Iterator[Pair]:
    """Yield one (representative example -> canonical encyclopedia) pair per live sense.

    Skipped entirely when the entry has no canonical encyclopedia rendition yet.
    """
    encyclopedia = entry.encyclopedia.canonical()
    if encyclopedia is None:
        return
    for sense, sid in live:
        rep = _representative_example(sense.examples)
        if rep is None:
            continue
        yield Pair(
            headword=entry.headword,
            headword_b=entry.headword,
            sense_a=sid,
            sense_b=None,
            text_a=rep.content.text,
            text_b=encyclopedia.content,
            span_a=rep.content.span,
            span_b=None,
            label=1,
            level_a=rep.reading_level.value,
            level_b=encyclopedia.reading_level.value,
            kind=PairKind.EXAMPLE_ENCYCLOPEDIA.value,
        )


def _pairs_for_entry(entry: Lexeme) -> Iterator[Pair]:
    """Yield every within-entry pair (WiC positive/hard-negative, gloss, encyclopedia)."""
    live = _live_senses(entry)
    yield from _wic_positive_pairs(entry, live)
    yield from _wic_hard_negative_pairs(entry, live)
    yield from _gloss_pairs(entry, live)
    yield from _encyclopedia_pairs(entry, live)


def _easy_negative_pairs(entries: Sequence[Lexeme], *, n: int, seed: int) -> Iterator[Pair]:
    """Yield up to ``n`` cross-headword, same-domain easy negatives per live sense.

    Builds one candidate pool per domain leaf from every live sense in ``entries`` that
    carries a domain tag and has at least one example. For each source sense, the
    candidates from *other* headwords in the same pool are sampled with a fresh
    ``random.Random`` seeded from ``(seed, domain, source_sense_id)``, so the draw for
    one sense never depends on how many other senses were visited before it, or in what
    order ``entries`` was given.
    """
    if n <= 0:
        return
    by_domain: dict[str, list[tuple[str, str, Rendition[Example]]]] = {}
    for entry in entries:
        for sense, sid in _live_senses(entry):
            if sense.domain is None:
                continue
            rep = _representative_example(sense.examples)
            if rep is None:
                continue
            by_domain.setdefault(sense.domain.value, []).append((entry.headword, sid, rep))

    for domain, pool in sorted(by_domain.items()):
        ordered_pool = sorted(pool, key=lambda item: item[1])
        for headword, sid, rep in ordered_pool:
            candidates = [c for c in ordered_pool if c[0] != headword]
            if not candidates:
                continue
            rng = random.Random(f"{seed}:{domain}:{sid}")  # noqa: S311 - sampling, not crypto
            chosen = rng.sample(candidates, min(n, len(candidates)))
            for other_headword, other_sid, other_rep in chosen:
                yield Pair(
                    headword=headword,
                    headword_b=other_headword,
                    sense_a=sid,
                    sense_b=other_sid,
                    text_a=rep.content.text,
                    text_b=other_rep.content.text,
                    span_a=rep.content.span,
                    span_b=other_rep.content.span,
                    label=0,
                    level_a=rep.reading_level.value,
                    level_b=other_rep.reading_level.value,
                    kind=PairKind.WIC_EASY_NEGATIVE.value,
                )


def _load_entries(store: LexemeStore, lexeme_ids: Sequence[str] | None) -> list[Lexeme]:
    """Return the entries to export, sorted by lexeme id for a deterministic run.

    Args:
        store: The store to read from.
        lexeme_ids: When given, restrict to these headwords/ids (absent ones are
            silently skipped, as other batch commands do); ``None`` reads the whole
            store.
    """
    if lexeme_ids is not None:
        entries = [entry for lid in lexeme_ids if (entry := store.read(lid)) is not None]
    else:
        entries = list(store.iter_entries())
    return sorted(entries, key=lambda entry: entry.lexeme_id)


def export_pairs(
    store: LexemeStore,
    out_path: Path,
    *,
    lexeme_ids: Sequence[str] | None = None,
    easy_negatives: int = 0,
    seed: int = 0,
) -> ExportPairsOutcome:
    """Write every mined pair to ``out_path`` as JSONL, in deterministic order.

    Args:
        store: The store to read from. Never written.
        out_path: Destination file; parent directories are created as needed.
        lexeme_ids: When given, restrict the export to these headwords/ids.
        easy_negatives: Sampled easy negatives per live domain-tagged sense (0 disables
            them entirely; this is the only optional pair kind).
        seed: Seed for easy-negative sampling. Ignored when ``easy_negatives`` is 0.

    Returns:
        Counts of what was written, by label and by pair kind.
    """
    entries = _load_entries(store, lexeme_ids)
    outcome = ExportPairsOutcome()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("wb") as handle:

        def emit(pair: Pair) -> None:
            handle.write(orjson.dumps(pair))
            handle.write(b"\n")
            outcome.record(pair)

        for entry in entries:
            outcome.entries_scanned += 1
            entry_pairs = list(_pairs_for_entry(entry))
            if entry_pairs:
                outcome.entries_with_pairs += 1
            for pair in entry_pairs:
                emit(pair)

        for pair in _easy_negative_pairs(entries, n=easy_negatives, seed=seed):
            emit(pair)

    return outcome
