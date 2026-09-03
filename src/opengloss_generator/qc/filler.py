"""F8 — ``qc filler``: a corpus-level, model-free filler detector.

``workflows/content_hygiene.py``'s ``stilted_examples`` step catches *known* academic
tells (``STILTED_RE`` — "researchers", "participants", "the study") in canonical
examples, one entry at a time. This module catches the tells nobody wrote a regex for
yet, by looking at the *whole store at once*: a 4-gram or a sentence opener that recurs
far more often than chance across thousands of independently-generated renditions is a
model habit, not a coincidence, whatever words it happens to use. It makes no model
call and mutates nothing unless ``--flag``/``--unflag`` is given.

Two passes over the store (``analyze_filler``), exactly as the plan (F8) specifies:

1. **Count.** Every non-retired sense's example renditions and every entry's
   encyclopedia renditions are split into sentences (``.``/``!``/``?``/``…``-delimited).
   For each sentence: the set of its 4-grams (word 4-tuples, deduplicated *within* the
   sentence, so one repetitive sentence cannot inflate a count on its own) and its
   2-word and 3-word openers are tallied. A key's frequency is
   ``(sentences containing it) / (total sentences scanned)`` — a document-frequency, not
   a raw occurrence count, which is what "appearing in >X% of sentences" means.
2. **Score.** A key clears the filler bar when its frequency exceeds
   :attr:`FillerConfig.ngram_freq_threshold` (4-grams, default 0.05%) or
   :attr:`FillerConfig.opener_freq_threshold` (openers, default 0.5%) *and* its raw count
   is at least :attr:`FillerConfig.min_count` (default 5 — a floor against a small store
   where one repeated pair alone would clear a tiny denominator's threshold). Every
   rendition with at least one over-threshold sentence is an "offending" rendition;
   ``--flag`` sets :data:`~opengloss_generator.schema.QAFlag.OG_FILLER` on its
   :class:`~opengloss_generator.schema.Assessment` (creating one if absent), so a later
   rewrite pass — outside this module's scope — can target exactly these renditions.
   ``--unflag`` reverses it, by recomputing the same offending set and removing the flag
   from it; both are idempotent (:meth:`~opengloss_generator.schema.Assessment.flag` is
   already idempotent, and a removal that finds nothing to remove writes nothing).

Alongside the corpus-level detector, every entry with at least one live sense gets two
diagnostic scores over the concatenation of its own examples and encyclopedia text —
report-only, never a flagging input, in the spirit of the heuristic gate the plan cites
(``alea-quality-model``'s ``estimate_document_quality``):

* ``uniqueness`` — type/token ratio (distinct words / total words).
* ``information_density`` — a banded score on words-per-sentence: ``0.8`` inside the
  10-30 word "ideal" band, ``0.6`` inside the wider 5-40 word "acceptable" band, ``0.4``
  outside both. A short, choppy or run-on entry scores low regardless of vocabulary.

Neither score currently gates ``--flag``; the plan asks only that they be measured and
reported, since no threshold for them was given.

**Report JSON shape** (``--out``)::

    {
      "totals": {
        "entries_scanned": 300, "senses_live": 512, "units_scanned": 1204,
        "sentences_scanned": 1301, "renditions_flagged_candidates": 7
      },
      "config": {"ngram_n": 4, "opener_lengths": [2, 3], "ngram_freq_threshold": 0.0005,
                 "opener_freq_threshold": 0.005, "min_count": 5, "max_examples": 3,
                 "ideal_sentence_words": [10, 30], "acceptable_sentence_words": [5, 40]},
      "filler_ngrams": [
        {"phrase": "is a type of", "n": 4, "count": 9, "frequency": 0.0069,
         "example_rendition_ids": ["abseil:verb:0#neutral/plain#0", "...", "..."]}
      ],
      "filler_openers": {
        "2": [{"phrase": "it is", "length": 2, "count": 14, "frequency": 0.0108,
               "example_rendition_ids": ["...", "...", "..."]}],
        "3": []
      },
      "entry_scores": {
        "distribution": {
          "uniqueness": {"count": 300, "mean": 0.87, "min": 0.41, "p10": 0.71,
                          "median": 0.89, "p90": 0.98, "max": 1.0},
          "information_density": {"count": 300, "mean": 0.71, "...": "..."}
        },
        "worst_by_uniqueness": [
          {"lexeme_id": "...", "uniqueness": 0.41, "information_density": 0.6,
           "avg_sentence_words": 6.2, "word_count": 34}
        ]
      }
    }

A rendition id for an encyclopedia (or gloss) text is the store's own
:func:`~opengloss_generator.identity.rendition_id`. An *example* rendition has no such
id (:meth:`~opengloss_generator.schema.Lexeme.rendition_ids` deliberately excludes
examples — several may share one ``(level, register)`` key), so this module mints its
own: the owning sense id, the ``(level, register)`` key, and the example's position in
its sense's example list, e.g. ``abseil:verb:0#neutral/plain#0``. It identifies a
rendition for a human reading the report; flagging itself re-locates the rendition by
its stored ``(level, register, text)`` rather than trusting that position has not
shifted since the report was built (the same discipline
``workflows/graph_hygiene.py`` uses for its plan-then-apply passes, D-31).
"""

from __future__ import annotations

import asyncio
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opengloss_generator.identity import encyclopedia_owner_id, rendition_id, slugify
from opengloss_generator.log import get_logger
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Assessment, QAFlag

if TYPE_CHECKING:
    from collections.abc import Iterable

    from opengloss_generator.schema import Lexeme, ReadingLevel, Register
    from opengloss_generator.store import LexemeStore

__all__ = [
    "CalibrationPoint",
    "FillerConfig",
    "FillerFlagOutcome",
    "FillerReport",
    "analyze_filler",
    "apply_filler_flags",
    "calibrate_thresholds",
    "phrases_in",
]

_LOG = get_logger(__name__)

#: The three scopes ``fields`` (``analyze_filler``, ``calibrate_thresholds``, ``qc
#: filler``'s own ``--fields``) accepts. D-66: the corpus-level detector's frequency
#: denominator is the *whole* scan, so counting encyclopedia and example sentences
#: together lets an encyclopedia template (92.3% of encyclopedia renditions, D-60) drown
#: out the much rarer example-only tells this feature exists to fix; restricting the scan
#: to one field changes which n-grams and openers clear the bar, not just which
#: renditions get flagged.
_VALID_FIELDS = ("examples", "encyclopedia", "all")

#: A word is a run of word characters, apostrophes included, exactly as
#: ``readability._TOKEN_RE`` defines one — duplicated rather than imported so this
#: module does not depend on another module's private name.
_WORD_RE = re.compile("[\\w'\u2019]+", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?…]+")


def _words(text: str) -> list[str]:
    """Return the lowercased word tokens of ``text``, in order."""
    return [token.lower() for token in _WORD_RE.findall(text) if any(ch.isalnum() for ch in token)]


def _split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentence-like chunks on ``.``/``!``/``?``/``…``.

    A text with words but no terminal punctuation (a bare canonical example) is one
    sentence, not zero — mirroring
    :func:`~opengloss_generator.readability.sentence_count`. A text with no words at all
    yields no sentences.
    """
    if not _words(text):
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if _words(part)]
    return parts if parts else [text.strip()]


def _sentence_ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    """Return the distinct ``n``-grams of ``tokens`` (deduplicated within the sentence)."""
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


@dataclass(slots=True, frozen=True)
class FillerConfig:
    """Thresholds and knobs for :func:`analyze_filler`.

    The threshold defaults were the plan's own numbers (D-60) until D-66 calibrated them
    against the full production store, scoped to ``fields="examples"`` (D-60's own
    measurement was 92.3% of encyclopedia renditions vs. 1.2% of example renditions at
    those numbers over the whole corpus — the two fields need different bars, and mixing
    them into one scan's frequency denominator was hiding that). ``ngram_freq_threshold``
    and ``opener_freq_threshold`` are now the D-66 calibration's chosen point (6.52% of
    example renditions flagged on the full store, `docs/DECISIONS.md` D-66's table): still
    the plan's own *shape* of rule (a document-frequency bar plus a count floor), just
    retuned for the field this project actually rewrites off it.

    Attributes:
        ngram_n: N-gram length counted for the filler-phrase detector.
        opener_lengths: Sentence-opener lengths (in words) counted alongside the n-gram.
        ngram_freq_threshold: An n-gram is filler once it appears in more than this
            share of scanned sentences (D-66: 0.025%, examples-scoped).
        opener_freq_threshold: Same, for an opener (D-66: 0.25%, examples-scoped).
        min_count: A key must also appear in at least this many sentences, whatever its
            frequency, so a small store's tiny denominator cannot turn one repeated pair
            into a "finding".
        max_examples: Example rendition ids kept per over-threshold finding.
        ideal_sentence_words: Inclusive words-per-sentence band scoring
            ``information_density`` at 0.8.
        acceptable_sentence_words: Wider inclusive band scoring 0.6; outside it, 0.4.
        worst_entries_reported: How many lowest-``uniqueness`` entries the report lists.
    """

    ngram_n: int = 4
    opener_lengths: tuple[int, ...] = (2, 3)
    ngram_freq_threshold: float = 0.00025
    opener_freq_threshold: float = 0.0025
    min_count: int = 5
    max_examples: int = 3
    ideal_sentence_words: tuple[int, int] = (10, 30)
    acceptable_sentence_words: tuple[int, int] = (5, 40)
    worst_entries_reported: int = 20

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view, for embedding in the report."""
        return {
            "ngram_n": self.ngram_n,
            "opener_lengths": list(self.opener_lengths),
            "ngram_freq_threshold": self.ngram_freq_threshold,
            "opener_freq_threshold": self.opener_freq_threshold,
            "min_count": self.min_count,
            "max_examples": self.max_examples,
            "ideal_sentence_words": list(self.ideal_sentence_words),
            "acceptable_sentence_words": list(self.acceptable_sentence_words),
        }


@dataclass(slots=True)
class _RenditionRef:
    """One example or encyclopedia rendition, identified well enough to re-locate it.

    ``report_id`` is for humans reading the report; re-locating a rendition to flag it
    uses ``(sense_id or None, level, style, text)`` instead (see the module docstring).
    """

    lexeme_id: str
    report_id: str
    kind: str  # "example" | "encyclopedia"
    sense_id: str | None
    level: ReadingLevel
    style: Register
    text: str


def _collect_refs(entries: Iterable[Lexeme]) -> list[_RenditionRef]:
    """Return one :class:`_RenditionRef` per example (live senses) or encyclopedia rendition."""
    refs: list[_RenditionRef] = []
    for entry in entries:
        for _, sense, sid in entry.iter_senses():
            if sense.retired:
                continue
            for index, rendition in enumerate(sense.examples):
                refs.append(
                    _RenditionRef(
                        lexeme_id=entry.lexeme_id,
                        report_id=(
                            f"{sid}#{rendition.reading_level.value}/{rendition.style.value}#{index}"
                        ),
                        kind="example",
                        sense_id=sid,
                        level=rendition.reading_level,
                        style=rendition.style,
                        text=rendition.content.text,
                    )
                )
        owner_id = encyclopedia_owner_id(entry.lexeme_id)
        for rendition in entry.encyclopedia:
            refs.append(
                _RenditionRef(
                    lexeme_id=entry.lexeme_id,
                    report_id=rendition_id(
                        owner_id, rendition.reading_level.value, rendition.style.value
                    ),
                    kind="encyclopedia",
                    sense_id=None,
                    level=rendition.reading_level,
                    style=rendition.style,
                    text=rendition.content,
                )
            )
    return refs


def _filter_fields(refs: list[_RenditionRef], fields: str) -> list[_RenditionRef]:
    """Restrict ``refs`` to one scan scope.

    Args:
        refs: Every collected rendition reference.
        fields: One of :data:`_VALID_FIELDS`. ``"all"`` returns ``refs`` unchanged.

    Returns:
        The refs of the requested kind, in their original order.
    """
    if fields == "all":
        return refs
    wanted = "example" if fields == "examples" else "encyclopedia"
    return [ref for ref in refs if ref.kind == wanted]


@dataclass(slots=True)
class _CorpusCounts:
    """Pass 1's output: how many sentences each n-gram / opener key appears in."""

    ngram_counts: Counter[tuple[str, ...]] = field(default_factory=Counter)
    opener_counts: dict[int, Counter[tuple[str, ...]]] = field(default_factory=dict)
    total_sentences: int = 0


def _count_corpus(refs: list[_RenditionRef], config: FillerConfig) -> _CorpusCounts:
    """Pass 1: tally sentence-level n-gram and opener document frequencies."""
    counts = _CorpusCounts(opener_counts={length: Counter() for length in config.opener_lengths})
    for ref in refs:
        for sentence in _split_sentences(ref.text):
            tokens = _words(sentence)
            if not tokens:
                continue
            counts.total_sentences += 1
            counts.ngram_counts.update(_sentence_ngrams(tokens, config.ngram_n))
            for length in config.opener_lengths:
                if len(tokens) >= length:
                    counts.opener_counts[length][tuple(tokens[:length])] += 1
    return counts


def _over_threshold(
    counter: Counter[tuple[str, ...]], *, total_sentences: int, threshold: float, min_count: int
) -> set[tuple[str, ...]]:
    """Return the keys of ``counter`` that clear both the frequency and count bars."""
    if total_sentences == 0:
        return set()
    return {
        key
        for key, count in counter.items()
        if count >= min_count and (count / total_sentences) > threshold
    }


@dataclass(slots=True)
class _Finding:
    """One over-threshold key's report row, and the offenders it touches."""

    key: tuple[str, ...]
    count: int
    example_report_ids: list[str] = field(default_factory=list)


def _locate_offenders(
    refs: list[_RenditionRef],
    *,
    over_ngrams: set[tuple[str, ...]],
    over_openers: dict[int, set[tuple[str, ...]]],
    config: FillerConfig,
) -> tuple[
    dict[tuple[str, ...], _Finding], dict[int, dict[tuple[str, ...], _Finding]], list[_RenditionRef]
]:
    """Pass 2: re-walk the corpus, this time only checking the over-threshold keys.

    Returns:
        The n-gram findings keyed by n-gram, the opener findings keyed by opener length
        then by opener, and the list of refs with at least one matching sentence.
    """
    ngram_findings: dict[tuple[str, ...], _Finding] = {
        key: _Finding(key=key, count=0) for key in over_ngrams
    }
    opener_findings: dict[int, dict[tuple[str, ...], _Finding]] = {
        length: {key: _Finding(key=key, count=0) for key in keys}
        for length, keys in over_openers.items()
    }
    offenders: list[_RenditionRef] = []

    for ref in refs:
        matched = False
        for sentence in _split_sentences(ref.text):
            tokens = _words(sentence)
            if not tokens:
                continue
            for gram in _sentence_ngrams(tokens, config.ngram_n) & over_ngrams:
                finding = ngram_findings[gram]
                finding.count += 1
                if len(finding.example_report_ids) < config.max_examples:
                    finding.example_report_ids.append(ref.report_id)
                matched = True
            for length, keys in over_openers.items():
                if len(tokens) < length or not keys:
                    continue
                opener = tuple(tokens[:length])
                if opener in keys:
                    finding = opener_findings[length][opener]
                    finding.count += 1
                    if len(finding.example_report_ids) < config.max_examples:
                        finding.example_report_ids.append(ref.report_id)
                    matched = True
        if matched:
            offenders.append(ref)

    return ngram_findings, opener_findings, offenders


@dataclass(slots=True)
class EntryScore:
    """The two report-only diagnostic scores for one entry."""

    lexeme_id: str
    word_count: int
    sentence_count: int
    avg_sentence_words: float
    uniqueness: float
    information_density: float

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view."""
        return {
            "lexeme_id": self.lexeme_id,
            "word_count": self.word_count,
            "sentence_count": self.sentence_count,
            "avg_sentence_words": round(self.avg_sentence_words, 3),
            "uniqueness": round(self.uniqueness, 4),
            "information_density": round(self.information_density, 4),
        }


def _information_density(avg_sentence_words: float, config: FillerConfig) -> float:
    """Band ``avg_sentence_words`` into a 0.8/0.6/0.4 score, per the module docstring."""
    ideal_lo, ideal_hi = config.ideal_sentence_words
    if ideal_lo <= avg_sentence_words <= ideal_hi:
        return 0.8
    ok_lo, ok_hi = config.acceptable_sentence_words
    if ok_lo <= avg_sentence_words <= ok_hi:
        return 0.6
    return 0.4


def _score_entries(refs: list[_RenditionRef], config: FillerConfig) -> dict[str, EntryScore]:
    """Compute uniqueness and information-density per entry over its own text alone."""
    texts_by_entry: dict[str, list[str]] = defaultdict(list)
    for ref in refs:
        texts_by_entry[ref.lexeme_id].append(ref.text)

    scores: dict[str, EntryScore] = {}
    for lexeme_id, texts in texts_by_entry.items():
        words = _words(" ".join(texts))
        word_count = len(words)
        sentence_count = sum(len(_split_sentences(text)) for text in texts)
        avg_sentence_words = word_count / sentence_count if sentence_count else 0.0
        uniqueness = len(set(words)) / word_count if word_count else 0.0
        scores[lexeme_id] = EntryScore(
            lexeme_id=lexeme_id,
            word_count=word_count,
            sentence_count=sentence_count,
            avg_sentence_words=avg_sentence_words,
            uniqueness=uniqueness,
            information_density=_information_density(avg_sentence_words, config),
        )
    return scores


#: `statistics.quantiles` needs at least two data points.
_MIN_VALUES_FOR_QUANTILES = 2


def _distribution(values: list[float]) -> dict[str, object]:
    """Return a mean/min/p10/median/p90/max summary of ``values``."""
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    quantiles = (
        statistics.quantiles(ordered, n=10, method="inclusive")
        if len(ordered) >= _MIN_VALUES_FOR_QUANTILES
        else None
    )

    def pct(index: int) -> float:
        return quantiles[index] if quantiles is not None else ordered[0]

    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 4),
        "min": round(ordered[0], 4),
        "p10": round(pct(0), 4),
        "median": round(statistics.median(ordered), 4),
        "p90": round(pct(8), 4),
        "max": round(ordered[-1], 4),
    }


@dataclass(slots=True)
class FillerReport:
    """Everything :func:`analyze_filler` measured; write-only via :meth:`as_dict`."""

    entries_scanned: int
    senses_live: int
    units_scanned: int
    sentences_scanned: int
    config: FillerConfig
    ngram_findings: list[_Finding]
    opener_findings: dict[int, list[_Finding]]
    entry_scores: dict[str, EntryScore]
    offending_refs: list[_RenditionRef]
    fields: str = "all"

    @property
    def offending_rendition_ids(self) -> set[str]:
        """Return the report ids of every rendition ``--flag`` would touch."""
        return {ref.report_id for ref in self.offending_refs}

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-able report described in the module docstring."""
        worst = sorted(self.entry_scores.values(), key=lambda score: score.uniqueness)[
            : self.config.worst_entries_reported
        ]
        uniqueness_values = [score.uniqueness for score in self.entry_scores.values()]
        density_values = [score.information_density for score in self.entry_scores.values()]
        avg_sentence_values = [score.avg_sentence_words for score in self.entry_scores.values()]

        def finding_dict(finding: _Finding, *, n: int) -> dict[str, object]:
            return {
                "phrase": " ".join(finding.key),
                "n": n,
                "count": finding.count,
                "frequency": round(finding.count / self.sentences_scanned, 6)
                if self.sentences_scanned
                else 0.0,
                "example_rendition_ids": finding.example_report_ids,
            }

        def by_count_then_phrase(finding: _Finding) -> tuple[int, tuple[str, ...]]:
            return (-finding.count, finding.key)

        ngram_rows = [
            finding_dict(f, n=self.config.ngram_n)
            for f in sorted(self.ngram_findings, key=by_count_then_phrase)
        ]
        opener_rows = {
            str(length): [
                finding_dict(f, n=length) for f in sorted(findings, key=by_count_then_phrase)
            ]
            for length, findings in self.opener_findings.items()
        }

        return {
            "totals": {
                "entries_scanned": self.entries_scanned,
                "senses_live": self.senses_live,
                "units_scanned": self.units_scanned,
                "sentences_scanned": self.sentences_scanned,
                "renditions_flagged_candidates": len(self.offending_refs),
                "fields": self.fields,
            },
            "config": self.config.as_dict(),
            "filler_ngrams": ngram_rows,
            "filler_openers": opener_rows,
            "entry_scores": {
                "distribution": {
                    "uniqueness": _distribution(uniqueness_values),
                    "information_density": _distribution(density_values),
                    "avg_sentence_words": _distribution(avg_sentence_values),
                },
                "worst_by_uniqueness": [score.as_dict() for score in worst],
            },
        }


def analyze_filler(
    store: LexemeStore,
    *,
    config: FillerConfig | None = None,
    core_words: set[str] | None = None,
    fields: str = "all",
) -> FillerReport:
    """Measure corpus-level filler and per-entry diagnostic scores. Reads only.

    Args:
        store: The store to read.
        config: Thresholds; defaults to :class:`FillerConfig`'s plan defaults.
        core_words: When given, restrict the scan to these headwords (as
            ``audit_store``'s ``core_words`` does), rather than the whole store.
        fields: Which renditions to scan — ``"examples"``, ``"encyclopedia"``, or
            ``"all"`` (the default, D-60's original behaviour). This is not just a filter
            on what gets reported: it changes the sentence-count denominator every
            frequency is measured against, and therefore which n-grams and openers clear
            the bar at all (D-66) — restricting to ``"examples"`` is how the encyclopedia's
            own template filler (92.3% of encyclopedia renditions, D-60) is kept from
            swamping the much rarer example-only tells this option exists to isolate.

    Returns:
        The full :class:`FillerReport`.

    Raises:
        ValueError: If ``fields`` is not one of :data:`_VALID_FIELDS`.
    """
    config = config or FillerConfig()
    if fields not in _VALID_FIELDS:
        message = f"unknown fields option {fields!r}; choose from {_VALID_FIELDS}"
        raise ValueError(message)
    if core_words is not None:
        core_ids = sorted({slugify(word) for word in core_words})
        entries: list[Lexeme] = [
            entry for lexeme_id in core_ids if (entry := store.read(lexeme_id)) is not None
        ]
    else:
        entries = list(store.iter_entries())

    senses_live = sum(
        1 for entry in entries for _, sense, _ in entry.iter_senses() if not sense.retired
    )
    refs = _filter_fields(_collect_refs(entries), fields)

    counts = _count_corpus(refs, config)
    over_ngrams = _over_threshold(
        counts.ngram_counts,
        total_sentences=counts.total_sentences,
        threshold=config.ngram_freq_threshold,
        min_count=config.min_count,
    )
    over_openers = {
        length: _over_threshold(
            counter,
            total_sentences=counts.total_sentences,
            threshold=config.opener_freq_threshold,
            min_count=config.min_count,
        )
        for length, counter in counts.opener_counts.items()
    }
    ngram_findings, opener_findings_by_length, offenders = _locate_offenders(
        refs, over_ngrams=over_ngrams, over_openers=over_openers, config=config
    )

    report = FillerReport(
        entries_scanned=len(entries),
        senses_live=senses_live,
        units_scanned=len(refs),
        sentences_scanned=counts.total_sentences,
        config=config,
        ngram_findings=list(ngram_findings.values()),
        opener_findings={
            length: list(findings.values())
            for length, findings in opener_findings_by_length.items()
        },
        entry_scores=_score_entries(refs, config),
        offending_refs=offenders,
        fields=fields,
    )
    _LOG.info(
        "qc_filler_analyzed",
        entries_scanned=report.entries_scanned,
        sentences_scanned=report.sentences_scanned,
        filler_ngrams=len(report.ngram_findings),
        offending_renditions=len(report.offending_refs),
        fields=fields,
    )
    return report


def phrases_in(text: str, report: FillerReport) -> list[str]:
    """Return every over-threshold phrase from ``report`` that appears in ``text``.

    A rewrite pass that reads :data:`~opengloss_generator.schema.QAFlag.OG_FILLER` off a
    stored rendition knows *that* it was flagged but not, from the flag alone, *which*
    corpus-level tell earned it — the flag carries no phrase. This re-walks ``text`` the
    same way :func:`analyze_filler`'s own passes do (sentence split, n-gram and opener
    extraction) and checks the result against the findings ``report`` already computed;
    it does not recompute what counts as filler, only where it shows up in one piece of
    text, so a rewrite prompt can name the specific phrase to avoid rather than say
    "this is filler" in the abstract.

    Args:
        text: The rendition text to check, exactly as stored.
        report: A :class:`FillerReport`, typically :func:`analyze_filler`'s own output
            against the same store the text was read from.

    Returns:
        The matching phrases, longest n-gram/opener first and, within a length, most
        frequent first, deduplicated. Empty when nothing in ``report`` matches ``text`` —
        which is not an error: it happens whenever the flag was set under a different
        config, or the store has moved on since ``report`` was built, and a caller should
        treat it as "no specific phrase to name" rather than a bug.
    """
    ngram_by_key = {finding.key: finding for finding in report.ngram_findings}
    opener_by_length = {
        length: {finding.key: finding for finding in findings}
        for length, findings in report.opener_findings.items()
    }
    matched: dict[tuple[str, ...], _Finding] = {}
    for sentence in _split_sentences(text):
        tokens = _words(sentence)
        if not tokens:
            continue
        for gram in _sentence_ngrams(tokens, report.config.ngram_n):
            finding = ngram_by_key.get(gram)
            if finding is not None:
                matched[gram] = finding
        for length, keys in opener_by_length.items():
            if len(tokens) < length or not keys:
                continue
            opener = tuple(tokens[:length])
            finding = keys.get(opener)
            if finding is not None:
                matched[opener] = finding
    ordered = sorted(matched.items(), key=lambda item: (-len(item[0]), -item[1].count))
    return [" ".join(key) for key, _ in ordered]


@dataclass(slots=True, frozen=True)
class CalibrationPoint:
    """One threshold combination's measured effect, from :func:`calibrate_thresholds`.

    Attributes:
        ngram_freq_threshold: The 4-gram frequency bar this point measured.
        opener_freq_threshold: The opener frequency bar this point measured.
        min_count: The floor count this point measured.
        units_scanned: Renditions in scope (after ``fields`` filtering).
        sentences_scanned: Sentences counted, the shared denominator every frequency in
            this point was measured against.
        renditions_flagged: How many of ``units_scanned`` would be ``--flag`` candidates
            at this threshold combination.
        flag_rate: ``renditions_flagged / units_scanned``, ``0.0`` if nothing was scanned.
        top_phrases: Up to ``top_n`` findings, by sentence count descending, each
            ``{"phrase", "n", "count", "frequency"}`` — the same shape
            :meth:`FillerReport.as_dict`'s finding rows use.
    """

    ngram_freq_threshold: float
    opener_freq_threshold: float
    min_count: int
    units_scanned: int
    sentences_scanned: int
    renditions_flagged: int
    flag_rate: float
    top_phrases: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view, for the D-66 calibration table."""
        return {
            "ngram_freq_threshold": self.ngram_freq_threshold,
            "opener_freq_threshold": self.opener_freq_threshold,
            "min_count": self.min_count,
            "units_scanned": self.units_scanned,
            "sentences_scanned": self.sentences_scanned,
            "renditions_flagged": self.renditions_flagged,
            "flag_rate": round(self.flag_rate, 4),
            "top_phrases": self.top_phrases,
        }


def calibrate_thresholds(
    store: LexemeStore,
    *,
    thresholds: Iterable[tuple[float, float, int]],
    fields: str = "examples",
    core_words: set[str] | None = None,
    top_n: int = 25,
    ngram_n: int = 4,
    opener_lengths: tuple[int, ...] = (2, 3),
) -> list[CalibrationPoint]:
    """Measure the flag rate at several thresholds in one read-only pass over the store.

    D-66: the plan's own thresholds (D-60) were never tuned against a judged sample, and
    flagging at them scoped to ``fields="examples"`` needs its own number, not the
    all-fields one. Pass 1 (sentence-level n-gram/opener counting) does not depend on the
    threshold at all, so it runs exactly once here regardless of how many
    ``(ngram_freq_threshold, opener_freq_threshold, min_count)`` triples are given — the
    only thing that differs per triple is which counted keys clear the bar (pass 2,
    :func:`_over_threshold`/:func:`_locate_offenders`), which is cheap, in-memory work.
    Calling :func:`analyze_filler` once per triple instead would re-read and re-tokenise
    the whole store every time, which is the difference that matters at production-store
    scale (tens of thousands of entries).

    Args:
        store: The store to read. Never written.
        thresholds: The ``(ngram_freq_threshold, opener_freq_threshold, min_count)``
            triples to measure, in the order given.
        fields: Scan scope, as :func:`analyze_filler`'s own parameter — defaults to
            ``"examples"`` here (unlike :func:`analyze_filler`'s ``"all"``) since D-66's
            whole point is calibrating the example-only number.
        core_words: When given, restrict the scan to these headwords.
        top_n: Findings kept per point's ``top_phrases``, by sentence count descending.
        ngram_n: N-gram length counted, shared by every threshold triple.
        opener_lengths: Sentence-opener lengths counted, shared by every triple.

    Returns:
        One :class:`CalibrationPoint` per triple in ``thresholds``, in the order given.

    Raises:
        ValueError: If ``fields`` is not one of :data:`_VALID_FIELDS`.
    """
    if fields not in _VALID_FIELDS:
        message = f"unknown fields option {fields!r}; choose from {_VALID_FIELDS}"
        raise ValueError(message)
    if core_words is not None:
        core_ids = sorted({slugify(word) for word in core_words})
        entries: list[Lexeme] = [
            entry for lexeme_id in core_ids if (entry := store.read(lexeme_id)) is not None
        ]
    else:
        entries = list(store.iter_entries())

    refs = _filter_fields(_collect_refs(entries), fields)
    counting_config = FillerConfig(ngram_n=ngram_n, opener_lengths=opener_lengths)
    counts = _count_corpus(refs, counting_config)

    points: list[CalibrationPoint] = []
    for ngram_threshold, opener_threshold, min_count in thresholds:
        point_config = FillerConfig(
            ngram_n=ngram_n,
            opener_lengths=opener_lengths,
            ngram_freq_threshold=ngram_threshold,
            opener_freq_threshold=opener_threshold,
            min_count=min_count,
        )
        over_ngrams = _over_threshold(
            counts.ngram_counts,
            total_sentences=counts.total_sentences,
            threshold=ngram_threshold,
            min_count=min_count,
        )
        over_openers = {
            length: _over_threshold(
                counter,
                total_sentences=counts.total_sentences,
                threshold=opener_threshold,
                min_count=min_count,
            )
            for length, counter in counts.opener_counts.items()
        }
        ngram_findings, opener_findings, offenders = _locate_offenders(
            refs, over_ngrams=over_ngrams, over_openers=over_openers, config=point_config
        )
        all_findings = [(finding, n) for finding in ngram_findings.values() for n in (ngram_n,)] + [
            (finding, length)
            for length, findings in opener_findings.items()
            for finding in findings.values()
        ]
        top = sorted(all_findings, key=lambda pair: -pair[0].count)[:top_n]
        top_phrases: list[dict[str, object]] = [
            {
                "phrase": " ".join(finding.key),
                "n": n,
                "count": finding.count,
                "frequency": round(finding.count / counts.total_sentences, 6)
                if counts.total_sentences
                else 0.0,
            }
            for finding, n in top
        ]
        points.append(
            CalibrationPoint(
                ngram_freq_threshold=ngram_threshold,
                opener_freq_threshold=opener_threshold,
                min_count=min_count,
                units_scanned=len(refs),
                sentences_scanned=counts.total_sentences,
                renditions_flagged=len(offenders),
                flag_rate=len(offenders) / len(refs) if refs else 0.0,
                top_phrases=top_phrases,
            )
        )
    _LOG.info(
        "qc_filler_calibrated",
        fields=fields,
        units_scanned=len(refs),
        sentences_scanned=counts.total_sentences,
        points=len(points),
    )
    return points


def _locate_rendition(entry: Lexeme, ref: _RenditionRef) -> Any:  # noqa: ANN401 - Rendition[str|Example]
    """Re-locate the stored rendition ``ref`` describes, by content, not position.

    Returns:
        The matching :class:`~opengloss_generator.schema.Rendition`, or ``None`` if the
        entry has changed underneath the report and nothing matches any more.
    """
    if ref.kind == "encyclopedia":
        return entry.encyclopedia.get(ref.level, ref.style)
    sense = next((sense for _, sense, sid in entry.iter_senses() if sid == ref.sense_id), None)
    if sense is None:
        return None
    return next(
        (
            rendition
            for rendition in sense.examples
            if rendition.reading_level is ref.level
            and rendition.style is ref.style
            and rendition.content.text == ref.text
        ),
        None,
    )


def _apply_ref(entry: Lexeme, ref: _RenditionRef, *, remove: bool) -> str:
    """Flag or unflag one rendition in place.

    Returns:
        ``"flagged"`` / ``"unflagged"`` if the assessment changed, ``"already"`` if it
        was already in the target state, or ``"not_found"`` if ``ref`` no longer matches
        anything on ``entry``.
    """
    rendition = _locate_rendition(entry, ref)
    if rendition is None:
        return "not_found"
    has_flag = (
        rendition.assessment is not None and QAFlag.OG_FILLER in rendition.assessment.qa_flags
    )
    if remove:
        if not has_flag:
            return "already"
        rendition.assessment.qa_flags = [
            flag for flag in rendition.assessment.qa_flags if flag is not QAFlag.OG_FILLER
        ]
        return "unflagged"
    if has_flag:
        return "already"
    if rendition.assessment is None:
        rendition.assessment = Assessment()
    rendition.assessment.flag(QAFlag.OG_FILLER)
    return "flagged"


@dataclass(slots=True)
class FillerFlagOutcome:
    """What :func:`apply_filler_flags` wrote (or would write, in a dry run)."""

    entries_scanned: int = 0
    entries_changed: int = 0
    renditions_flagged: int = 0
    renditions_unflagged: int = 0
    renditions_already: int = 0
    renditions_not_found: int = 0
    dry_run: bool = False
    stopped_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "renditions_flagged": self.renditions_flagged,
            "renditions_unflagged": self.renditions_unflagged,
            "renditions_already": self.renditions_already,
            "renditions_not_found": self.renditions_not_found,
            "dry_run": self.dry_run,
            "stopped_reason": self.stopped_reason,
        }


class _Tally:
    """Outcome counters, mutated only under a lock (mirrors ``graph_hygiene._Tally``)."""

    def __init__(self) -> None:
        """Start an empty tally."""
        self._lock = asyncio.Lock()
        self.outcome = FillerFlagOutcome()

    async def record(self, *, flagged: int, unflagged: int, already: int, not_found: int) -> None:
        """Fold one entry's per-rendition results into the counters."""
        async with self._lock:
            self.outcome.entries_scanned += 1
            if flagged or unflagged:
                self.outcome.entries_changed += 1
            self.outcome.renditions_flagged += flagged
            self.outcome.renditions_unflagged += unflagged
            self.outcome.renditions_already += already
            self.outcome.renditions_not_found += not_found


async def apply_filler_flags(
    store: LexemeStore,
    report: FillerReport,
    *,
    workers: int,
    remove: bool = False,
    stop_event: asyncio.Event | None = None,
) -> FillerFlagOutcome:
    """Write (``--flag``) or reverse (``--unflag``) :data:`QAFlag.OG_FILLER`.

    One entry per work item, its own lock held across read and write (D-31): each
    offending rendition named in ``report`` is re-located by content before it is
    touched, so an entry that changed after the report was built is skipped rather than
    mis-edited. Idempotent in both directions.

    Args:
        store: The store to write.
        report: A :class:`FillerReport` from :func:`analyze_filler` against this store.
        workers: Pool size.
        remove: Reverse the flag instead of setting it (``--unflag``).
        stop_event: When set, workers finish the entry in hand and stop pulling.

    Returns:
        The :class:`FillerFlagOutcome`.
    """
    refs_by_lexeme: dict[str, list[_RenditionRef]] = defaultdict(list)
    for ref in report.offending_refs:
        refs_by_lexeme[ref.lexeme_id].append(ref)

    tally = _Tally()

    async def handle(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                await tally.record(
                    flagged=0, unflagged=0, already=0, not_found=len(refs_by_lexeme[lexeme_id])
                )
                return
            flagged = unflagged = already = not_found = 0
            for ref in refs_by_lexeme[lexeme_id]:
                status = _apply_ref(entry, ref, remove=remove)
                if status == "flagged":
                    flagged += 1
                elif status == "unflagged":
                    unflagged += 1
                elif status == "already":
                    already += 1
                else:
                    not_found += 1
            if flagged or unflagged:
                store.write(entry)
        await tally.record(
            flagged=flagged, unflagged=unflagged, already=already, not_found=not_found
        )

    await run_pool(sorted(refs_by_lexeme), handle, workers=workers, stop_event=stop_event)
    outcome = tally.outcome
    outcome.dry_run = False
    if stop_event is not None and stop_event.is_set():
        outcome.stopped_reason = "stopped"
    _LOG.info("qc_filler_flags_applied", remove=remove, **outcome.as_dict())
    return outcome
