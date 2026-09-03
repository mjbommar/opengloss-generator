"""Workflow 5 — bring an existing store up to the v3 contract, one pass at a time.

Migration (``migrate.py``) produces structurally valid v3 entries, but three fields it
cannot fill honestly: the kind of an ambiguous multi-word headword, the controlled domain
tag of a sense whose legacy domain was free text, and the character span of an example
whose headword appears in an irregular form. This workflow fills them.

Every pass is **idempotent** and does the free work first:

``classify_kind``
    :func:`~opengloss_generator.migrate.classify_kind_deterministic` decides most
    headwords by rule at zero cost; only the residue — overwhelmingly multi-word forms,
    which are ambiguous between compound, phrasal verb and idiom — goes to the model, 50
    terms per call. The pass records the deterministic ratio as a metric, because that
    ratio *is* the cost model for this stage. The rule is given the entry's own prose as
    evidence (D-26), so a store whose headwords are all lowercased — v1.3 is one — has
    its proper nouns corrected here for free: migration writes no ``classify_kind``
    marker, so every migrated entry is re-examined on the first sweep whatever kind
    migration guessed.

``tag_domain``
    Only senses whose ``domain`` is ``None`` are sent, one call per entry covering all of
    them. The taxonomy itself never enters the per-call prompt (it lives in the cached
    instructions), and the answer is an enum, so nothing needs validating afterwards.

``hygiene``
    Runs between ``classify_kind`` and ``tag_domain`` — after kind is settled, before
    domain re-tagging, since this pass is what makes some senses need re-tagging. Four
    steps, cheapest first: (a) strip markdown from canonical prose (glosses, examples,
    the encyclopedia section, the lexical explanation); (b) drop relations whose target
    is a migration artifact (a hypernym-slot label like "descriptive term", cf.
    ``filters.py``'s frontier stoplist, or anything too long or sentence-shaped to be a
    word); (c) rewrite glosses that begin by naming their own headword — proper nouns are
    exempt (D-30), since their definitions legitimately name the entity — one nano call per
    entry covering every offending sense; (d) clear domain tags that are weak — ``None``
    already, the root's ``.general`` catch-all, or legacy-mapped without ever having
    passed through a real ``tag_domain`` verdict — so the next pass re-tags them for
    free. Findings 1 and the "also observed" note in ``docs/CORE-DIARY.md`` Iteration 1
    are what this pass answers.

    ``prompts.py`` and ``contracts.py`` are being edited concurrently elsewhere on this
    branch for unrelated work, so this pass's instructions text and its one small output
    contract (for step (c)) are defined in this module instead of those, to stay
    mergeable; they are ordinary module-private names with no other dependents and can
    move once that other work lands.

``spans``
    :func:`~opengloss_generator.spans.find_span` runs over every unplaced example for
    free; the residue goes to the model in batches of 40.

``repair``
    Runs last, after ``spans``. Two steps, free first: (a) within an entry, retire the
    later of any two non-retired senses whose canonical gloss is identical once case,
    whitespace, and a trailing period are normalised — never delete or renumber (D-1);
    (b) one nano call per entry, covering every non-retired sense still left with zero
    canonical examples, asking for one or two natural sentences per sense that contain
    the headword and fit that sense and no other — the entry's other senses are shown
    for context so the model can tell them apart, the same discipline
    ``prompts.SENSES_INSTRUCTIONS`` asks of the original senses stage. Each returned
    sentence becomes a canonical ``(neutral, plain)`` example; its span is found the same
    way the ``spans`` pass finds one, and a sentence the finder cannot place is still
    kept, with ``span=None`` — the ``spans`` pass's own model fallback gets another try
    at it on the next sweep. Like ``hygiene``'s step (c), this pass's one small output
    contract and its instructions are module-private: it is a single self-contained call
    site with no other dependents, so it has no reason to grow ``prompts.py`` or
    ``contracts.py``. Step (b) reuses ``StageName.HYGIENE``'s model policy (nano, low
    effort) for its call rather than adding a new stage just for one call site.

``rendition_hygiene``
    Runs last of all, after ``readability_hygiene``, and is ``hygiene``'s step (c) applied
    to the renditions rather than to the canonical gloss it was rewritten from. It goes
    last because it is the pass that *checks the form* of stored prose, and the pass that
    *rewrites* prose must therefore run before it, not after (D-47). The defect is measured
    (``docs/CORE-DIARY.md`` iteration 4, finding 1): canonical glosses open with their own
    headword 2.7% of the time, but their non-canonical *renditions* 10-15% of the time at
    every target, because a ten-word sentence budget pulls the model straight to "A ban is
    an order to stop." ``workflows/enrich.py`` now catches that at generation time (D-39);
    this pass is for what is already on disk. One nano call per entry lists every
    offending gloss rendition with the reading level and register it must stay at, the
    rewrites are markdown-stripped and applied, each one is re-scored for readability, and
    :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_INITIAL` is set or cleared to
    match what the new text actually is. Proper nouns are exempt, as everywhere else
    (D-30). The superseded text is kept in a zero-cost ``Provenance.note``, exactly as
    ``hygiene`` keeps a superseded gloss.

    A second, free step rides along in the same pass (D-59, F7): every stored non-``plain``
    gloss rendition is measured with :func:`~opengloss_generator.hygiene.is_near_copy`
    against its sense's canonical gloss, and
    :data:`~opengloss_generator.schema.QAFlag.OG_NEAR_COPY` is set or cleared to match.
    Unlike the headword-initial step this one spends nothing: a paraphrase a model was
    already told to write differently is not made better by asking it again in the same
    words, so there is no rewrite call to make, only a verdict to record for a later,
    dedicated rewrite pass to act on. It runs on every sweep rather than being gated by an
    attempt marker, since there is no cost to bound.

``readability_hygiene``
    Runs before ``rendition_hygiene``, and after everything else. It used to run last, on
    the argument that the more expensive of the two rendition-reading passes should not
    spend fixing text the other was about to rewrite; measured on the 10K core that cost
    more than it saved, because a readability rewrite of a hard gloss lands on "A ban is
    an order to stop." and nothing then revisited it (4,546 headword-initial gloss
    renditions became 6,480). The pass that rewrites prose runs first and the pass that
    checks the form of stored prose runs last (D-47), and this pass's own rewrites are
    held to the headword-initial rule as well: its instructions carry
    ``RENDITIONS_INSTRUCTIONS``' own sentence for it, and a gloss rewrite that opens with
    the headword is refused the way one that reads no easier is. Every rendition of
    every text-bearing field (gloss, examples, encyclopedia, lexical explanation) whose
    ``Assessment.qa_flags`` carries
    :data:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS` — the ~4-8% left over
    after generation time's own single retry (``workflows/enrich.py``) — is collected per
    entry and rewritten in one call on the ``RENDITIONS`` policy (luna): this is prose for
    an audience, not a structural verdict, so it gets the same model the original
    renditions were written by, not ``hygiene``'s nano. An entry whose flagged set exceeds
    roughly 3,000 words of source text is split into two calls instead of one, so neither
    is truncated. The prompt lists each offender as its field, its reading level and
    register, and the Flesch-Kincaid grade it measured against its band's upper bound,
    reusing the exact reading-level constraint text and field-meaning text
    :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` already gives the
    original generation call, and the exact wording
    :func:`~opengloss_generator.prompts.build_readability_feedback` already gives a
    first-pass retry, rather than drifting into a third phrasing of either. Every returned
    rewrite is markdown-stripped, re-measured with the headword scored as one syllable,
    and kept only if its grade is lower than what is already stored — the better of old
    and new, never a blind overwrite. An example's rewrite carries one more condition:
    :func:`~opengloss_generator.spans.find_span` is re-run over it, and a rewrite the
    finder cannot place — the rewrite lost the headword — is discarded outright, the old
    example kept untouched whatever its grade. Whatever text ends up stored is re-scored
    and :data:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS` is set or cleared
    to match, against the same ``tolerance``
    :class:`~opengloss_generator.config.ReadabilityConfig` applies at generation time. The
    superseded text is kept in a zero-cost ``Provenance.note``, exactly as
    ``rendition_hygiene`` keeps one.

Idempotence signals differ per pass because the fields differ. ``domain`` and ``span``
are nullable, so "already done" is visible in the data. ``kind`` is not — the schema
requires it — so the ``classify_kind`` pass writes a ``classify_kind`` provenance record
and skips entries that already carry one. The ``spans`` pass uses the same marker for its
*model* fallback only, so an example the model could not place is not re-billed on every
sweep; the free finder still runs over everything, every time. ``hygiene`` follows the
same rule as ``spans``: steps (a), (b) and (d) are free and re-examine everything on
every sweep (idempotent because they leave nothing behind for themselves to redo); step
(c) writes a ``hygiene`` provenance record once it has made its one call for an entry, so
a gloss the model did not usefully rewrite is not re-billed either.

``repair`` mixes both styles. Step (a) is naturally idempotent: a sense already retired
is skipped, so a re-run never finds the same duplicate pair twice — no marker needed.
Step (b) needs one, for the same reason ``hygiene``'s step (c) and ``spans``' fallback
do: a sense the model failed to usefully answer for would otherwise be re-sent every
sweep, forever. But since step (b)'s call is stamped ``stage=StageName.HYGIENE`` (it
reuses that stage's policy rather than adding one of its own), the marker cannot be the
stage alone — that would collide with the hygiene pass's own step-(c) record on any entry
that happened to get both. It is instead a private sentinel written to that call
record's ``note`` field (``_REPAIR_EXAMPLES_NOTE``), checked by ``_has_repaired`` rather
than the generic ``_has_run``. ``rendition_hygiene`` is a third call site stamped with
that same stage and so carries a sentinel of its own (``_RENDITION_HYGIENE_PREFIX``); it
is also the one pass that writes its entry even when its call changed nothing, since the
marker is the only thing that call bought. ``readability_hygiene`` follows the same rule
but is stamped ``stage=StageName.RENDITIONS`` instead of ``HYGIENE`` — it reuses that
stage's policy, not hygiene's nano one — so its own sentinel
(``_READABILITY_HYGIENE_PREFIX``) cannot collide with any of the three ``HYGIENE``-stamped
ones above, or with an ordinary ``enrich.py`` rendition-generation record, which carries
no note at all.

Those two sentinels are not booleans (D-47). Each is written as
``<pass>:<digest>;attempts=<n>``, where the digest hashes the sorted rendition ids the
pass was answering for on that sweep. An entry is skipped only when what offends *now*
hashes to what the entry's most recent marker was written for; a set that has changed —
because another pass rewrote text underneath this one, or because this one's own rewrite
did not take — earns one more attempt, on the current offenders only. The attempt count in
the same note bounds that at ``_HYGIENE_MAX_ATTEMPTS`` (two) per entry, after which
whatever still offends is left flagged rather than billed a third time. A marker written
before D-47 reads ``<pass>:rewritten``, which no digest equals, so every entry the old
boolean stamped is due exactly one more attempt and no more.

Concurrency and locking (D-31)
------------------------------

Every pass drives its entries through :func:`~opengloss_generator.runner.run_pool` at the
configured worker count. The unit of work is one entry, and the handler holds that
entry's lock across the whole of it — read, deterministic work, model call, write::

    async with store.locked(lexeme_id):
        entry = store.read(lexeme_id)
        ...                      # deterministic work, then the model call if one is due
        store.write(entry)

Holding the lock across the model call is deliberate. Per-entry contention is nil — an id
is queued once per pass, and the passes run one after another — so the long hold costs
only a lock file that lives for the duration of one call. What it buys is that no entry is
ever read outside the lock it is written under. Reading first and locking only around the
write, which is what three of these four passes used to do, silently drops whatever a
concurrent worker (or a second retrofit process over the same store) wrote in between.

The one exception is ``classify_kind``'s residue batch, which decides 50 entries in a
single call and so cannot hold 50 locks across it: it re-reads each entry under that
entry's own lock and applies the verdict there, which is still read-modify-write inside
one lock.

A pass's counters are accumulated by :class:`_Tally`, which mutates them only while
holding an ``asyncio.Lock``. A budget stop is not an error here: ``run_pool`` turns
:class:`~opengloss_generator.errors.BudgetExceededError` into a clean stop of the pool,
the pass records ``stopped_reason="budget"``, and :func:`run_retrofit` returns the outcome
it has rather than raising — later passes are skipped, and since each pass writes its
idempotence marker before the stop, relaunching resumes without re-billing anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import prompts, spans
from opengloss_generator.contracts import (
    KIND_BATCH_SIZE,
    SPAN_BATCH_SIZE,
    DraftDomainTags,
    DraftKindBatch,
    DraftSpanBatch,
)
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.hygiene import is_headword_initial, is_near_copy
from opengloss_generator.identity import (
    encyclopedia_owner_id,
    explanation_owner_id,
    rendition_id,
    sense_id,
)
from opengloss_generator.log import get_logger
from opengloss_generator.migrate import classify_kind_deterministic, entry_evidence
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.readability import flesch_kincaid_grade, grade_band, word_count
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    CANONICAL_KEY,
    Assessment,
    EntityType,
    Example,
    LexemeKind,
    POSEntry,
    ProperNounInfo,
    Provenance,
    QAFlag,
    ReadingLevel,
    Register,
    Rendition,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.taxonomy import TAXONOMY_VERSION, is_general

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

    from opengloss_generator.schema import Lexeme
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["PassResult", "RetrofitOutcome", "RetrofitPass", "run_retrofit"]

_LOG = get_logger(__name__)

DETERMINISTIC_MODEL = "rule:classify_kind_deterministic"

#: How much of a residue term's own gloss the batched classifier is shown. One short
#: snippet per term — roughly 30 extra input tokens — is what separates "einstein" the
#: unit of radiant energy from "Einstein" the physicist; the entry's encyclopedia section
#: would cost two orders of magnitude more for the same decision.
EVIDENCE_SNIPPET_CHARS = 120

#: How often a running pass logs its progress, in entries. A ten-thousand-entry pass is
#: otherwise silent for hours; twenty lines per pass is enough to watch a rate.
PROGRESS_EVERY = 500


class RetrofitPass:
    """Names of the passes ``run_retrofit`` can select between."""

    CLASSIFY_KIND = StageName.CLASSIFY_KIND.value
    HYGIENE = StageName.HYGIENE.value
    TAG_DOMAIN = StageName.TAG_DOMAIN.value
    SPANS = StageName.SPANS.value
    #: Not a ``StageName`` value — this pass reuses ``StageName.HYGIENE``'s model policy
    #: for its one call site rather than adding a stage of its own (see the module
    #: docstring), so its pass name is a plain string instead of an enum value's.
    REPAIR = "repair"
    #: Not a ``StageName`` value either, and for the same reason as ``REPAIR``.
    RENDITION_HYGIENE = "rendition_hygiene"
    #: Not a ``StageName`` value either: this pass reuses ``StageName.RENDITIONS``'s model
    #: policy (luna) instead of adding a stage of its own, since it is rewriting prose for
    #: an audience rather than deciding a structural verdict (see the module docstring).
    READABILITY_HYGIENE = "readability_hygiene"

    #: The order the passes run in. ``readability_hygiene`` runs *before*
    #: ``rendition_hygiene`` (D-47): the pass that rewrites prose goes first, and the
    #: pass that checks the form of stored prose goes last, so a readability rewrite that
    #: opens with the headword is caught in the same sweep that produced it.
    ALL: tuple[str, ...] = (
        CLASSIFY_KIND,
        HYGIENE,
        TAG_DOMAIN,
        SPANS,
        REPAIR,
        READABILITY_HYGIENE,
        RENDITION_HYGIENE,
    )


@dataclass(slots=True)
class PassResult:
    """Counts and cost for one retrofit pass.

    Attributes:
        stopped_reason: ``None`` when the pass ran to completion; ``"budget"`` when the
            run's ceiling was reached mid-pass; ``"stopped"`` when the caller's stop
            event was set. A stopped pass still reports everything it did before it
            stopped, and everything it wrote is on disk.
    """

    name: str
    entries_scanned: int = 0
    entries_changed: int = 0
    items_changed: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    stopped_reason: str | None = None


@dataclass(slots=True)
class RetrofitOutcome:
    """What a retrofit run did, per pass."""

    passes: dict[str, PassResult] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        """Return the total cost of every pass that ran."""
        return sum(result.cost_usd for result in self.passes.values())

    @property
    def stopped_reason(self) -> str | None:
        """Return why the run stopped early, or ``None`` if every selected pass ran.

        A caller that wants its run summary to say "budget" rather than "completed" reads
        this: a budget stop is reported, not raised (see the module docstring).
        """
        for result in self.passes.values():
            if result.stopped_reason is not None:
                return result.stopped_reason
        return None

    @property
    def calls(self) -> int:
        """Return the total model calls made by every pass that ran."""
        return sum(result.calls for result in self.passes.values())

    def counts(self) -> dict[str, int]:
        """Return ``{pass name: items changed}`` for a run summary."""
        return {name: result.items_changed for name, result in self.passes.items()}


def _marker(stage: StageName) -> Provenance:
    """Return the zero-cost provenance record that marks a free pass as done."""
    return Provenance(
        stage=stage,
        model=DETERMINISTIC_MODEL,
        prompt_version=PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
    )


def _has_run(entry: Lexeme, stage: StageName) -> bool:
    """Return whether a pass has already recorded provenance on this entry."""
    return any(record.stage is stage for record in entry.provenance.values())


# --------------------------------------------------------------------------------------
# Offending-set markers, shared by the two passes that rewrite stored renditions (D-47)
# --------------------------------------------------------------------------------------
#
# A plain "this pass has visited this entry" boolean is the wrong marker for a pass whose
# work another pass can undo. `rendition_hygiene` stamped one, `readability_hygiene` then
# rewrote renditions into "A ban is an order ..." forms behind it, and nothing revisited
# them: measured on the 10K core, headword-initial gloss renditions went 4,546 -> 6,480.
# Reordering the two passes (see `RetrofitPass.ALL`) fixes the common case; this marker
# fixes the general one. It records *which* renditions the pass was answering for, as a
# hash, so "already tried" means "already tried this exact set" rather than "already
# tried this entry", and a set that has changed since — a new offender, or one fewer —
# earns one more attempt on whatever is offending now.

#: How many attempts either pass will make on one entry before leaving what is still
#: offending flagged rather than billing a third answer for it. The count is per entry
#: rather than per rendition: a second sentinel record per rendition id would bound it
#: more precisely, but the note already has to carry the digest, and one integer beside
#: it is the whole of the bookkeeping this way (D-47).
_HYGIENE_MAX_ATTEMPTS = 2

#: Separates the offending-set digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR = ";attempts="


@dataclass(frozen=True, slots=True)
class _HygieneMarker:
    """The most recent marker one rendition-rewriting pass left on an entry.

    Attributes:
        digest: The offending-set hash the marker was written for. A marker written
            before D-47 carries the literal ``rewritten``, which no digest can equal, so
            such an entry is due exactly one more attempt on whatever offends now.
        attempts: How many attempts this pass has made on this entry, this marker's own
            included. A pre-D-47 marker counts as one.
    """

    digest: str
    attempts: int


def _offender_digest(offender_ids: Iterable[str]) -> str:
    """Return a stable short hash of the ids a pass is about to answer for.

    Args:
        offender_ids: The rendition ids of everything the pass found to fix on this
            sweep, in any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined ids. Sorted so
        the digest does not depend on document order, and SHA-256 rather than
        :func:`hash` because the value is written to disk and compared across processes.
    """
    joined = "\n".join(sorted(offender_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _latest_hygiene_marker(entry: Lexeme, prefix: str) -> _HygieneMarker | None:
    """Return the last marker ``prefix``'s pass wrote on an entry, parsed.

    Args:
        entry: The entry to inspect.
        prefix: The pass's note prefix, ``rendition_hygiene`` or ``readability_hygiene``.

    Returns:
        The most recent marker, or ``None`` if this pass has never visited the entry.
        Provenance ids are assigned in insertion order and never reused, so the last
        matching record in the table is the most recently written one.
    """
    latest: _HygieneMarker | None = None
    for record in entry.provenance.values():
        note = record.note or ""
        if not note.startswith(f"{prefix}:"):
            continue
        digest, _, attempts = note[len(prefix) + 1 :].partition(_ATTEMPTS_SEPARATOR)
        latest = _HygieneMarker(digest=digest, attempts=int(attempts) if attempts.isdigit() else 1)
    return latest


def _hygiene_attempt_due(entry: Lexeme, prefix: str, offender_ids: Sequence[str]) -> str | None:
    """Return the marker note to stamp on this entry's next attempt, or ``None``.

    An entry is due an attempt when it has something to fix and either the pass has never
    visited it, or the set of things to fix has changed since the pass last answered for
    it — and it has not already had :data:`_HYGIENE_MAX_ATTEMPTS` of them.

    Args:
        entry: The entry being considered.
        prefix: The pass's note prefix, ``rendition_hygiene`` or ``readability_hygiene``.
        offender_ids: The rendition ids offending *now*, which is exactly what the
            attempt would cover.

    Returns:
        The note to write on the call's provenance record — ``<prefix>:<digest>;attempts=
        <n>`` — or ``None`` when the entry must be skipped, which is also the "do not
        bill this" signal for the caller.
    """
    if not offender_ids:
        return None
    digest = _offender_digest(offender_ids)
    marker = _latest_hygiene_marker(entry, prefix)
    if marker is None:
        return f"{prefix}:{digest}{_ATTEMPTS_SEPARATOR}1"
    if marker.digest == digest or marker.attempts >= _HYGIENE_MAX_ATTEMPTS:
        return None
    return f"{prefix}:{digest}{_ATTEMPTS_SEPARATOR}{marker.attempts + 1}"


class _Tally:
    """One pass's counters, mutated only under a lock.

    Single-threaded asyncio does make ``counter += 1`` atomic on its own — nothing else
    can run between the read and the write of an await-free statement — but a pass's
    counters are touched by many handlers around many awaits, and that guarantee is a
    property of the interpreter rather than of this code. Every mutation therefore goes
    through this class and happens inside :attr:`_lock`, so the discipline is visible,
    testable, and does not quietly break the first time a handler grows an ``await``
    between reading a counter and writing it back.

    Args:
        name: The pass this tally belongs to.
    """

    def __init__(self, name: str) -> None:
        """Start an empty result for the named pass."""
        self._lock = asyncio.Lock()
        self._result = PassResult(name=name)
        self._visited = 0

    @property
    def result(self) -> PassResult:
        """Return the accumulated result; read it once the pool has drained."""
        return self._result

    async def entry(
        self,
        *,
        scanned: bool = True,
        items_changed: int = 0,
        metrics: Mapping[str, float] | None = None,
    ) -> None:
        """Fold one visited entry into the pass result.

        Args:
            scanned: Whether this entry counts as scanned by the pass.
            items_changed: How many things changed in it; a non-zero count also makes it
                one changed entry.
            metrics: Per-step counters to add to :attr:`PassResult.metrics`.
        """
        async with self._lock:
            if scanned:
                self._visited += 1
                self._result.entries_scanned += 1
            if items_changed:
                self._result.entries_changed += 1
                self._result.items_changed += items_changed
            for key, value in (metrics or {}).items():
                self._result.metrics[key] = self._result.metrics.get(key, 0.0) + value
            if self._visited % PROGRESS_EVERY == 0:
                _LOG.info(
                    "retrofit_pass_progress",
                    pass_name=self._result.name,
                    entries_done=self._visited,
                    entries_changed=self._result.entries_changed,
                    items_changed=self._result.items_changed,
                    calls=self._result.calls,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def note_stop(self, reason: str) -> None:
        """Record why the pass stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def _drive[T](
    items: Sequence[T],
    handler: Callable[[T], Awaitable[None]],
    tally: _Tally,
    *,
    workers: int,
    stop_event: asyncio.Event | None,
) -> None:
    """Run one pass's handler over ``items`` through the bounded pool.

    ``run_pool`` already treats :class:`BudgetExceededError` as a clean stop of the whole
    pool rather than an error to propagate, so the wrapper here exists only to record
    *why* the pass stopped before the exception is swallowed.

    Args:
        items: The work items — usually lexeme ids, or batches of them.
        handler: The per-item coroutine function.
        tally: The pass tally, which learns the stop reason.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.
    """

    async def guarded(item: T) -> None:
        try:
            await handler(item)
        except BudgetExceededError:
            await tally.note_stop("budget")
            raise

    await run_pool(items, guarded, workers=workers, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        await tally.note_stop("stopped")


#: One pass, as :func:`run_retrofit` calls it. Every pass takes the store, the runner and
#: the id list positionally and the pool settings by keyword.
type _PassFn = Callable[..., Awaitable[PassResult]]


async def run_retrofit(
    store: LexemeStore,
    runner: StageRunner,
    *,
    only: Iterable[str] | None = None,
    lexeme_ids: Iterable[str] | None = None,
    limit: int | None = None,
    workers: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> RetrofitOutcome:
    """Run the retrofit passes over a store.

    Args:
        store: The store to upgrade. Every entry is read, worked on, and written inside
            one hold of its own lock (see the module docstring).
        runner: The stage runner.
        only: Pass names to run; defaults to all of :attr:`RetrofitPass.ALL`, in that
            order (kind, then hygiene, then domain, then spans, then repair, then
            readability hygiene, then rendition hygiene — hygiene runs before domain
            because it is what makes some senses need re-tagging, repair runs after spans
            so its duplicate check sees every other pass's writes, and the two
            rendition-reading passes run after repair, readability hygiene first because
            it rewrites prose and rendition hygiene last because it checks the form of
            stored prose, so a rewrite that opens with the headword is caught in the same
            sweep that produced it (D-47)).
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        limit: Visit at most this many entries per pass.
        workers: Pool size for every pass; defaults to the runner's configured
            ``concurrency.workers``.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it
            from outside (the CLI passes its session's event, which ``SIGINT`` sets).

    Returns:
        A :class:`RetrofitOutcome` carrying counts and cost per pass. If a pass stopped
        early its ``stopped_reason`` says why and the remaining passes are skipped; the
        outcome is still returned rather than raised, so a partial run reports what it
        managed to do.

    Raises:
        ValueError: If ``only`` names a pass that does not exist.
    """
    selected = tuple(only) if only is not None else RetrofitPass.ALL
    unknown = sorted(set(selected) - set(RetrofitPass.ALL))
    if unknown:
        raise ValueError(f"unknown retrofit pass(es): {unknown}")

    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    if limit is not None:
        ids = ids[:limit]
    pool_size = runner.config.concurrency.workers if workers is None else workers

    outcome = RetrofitOutcome()
    for name in RetrofitPass.ALL:
        if name not in selected:
            continue
        runnable: _PassFn
        if name == RetrofitPass.CLASSIFY_KIND:
            runnable = _classify_kind_pass
        elif name == RetrofitPass.HYGIENE:
            runnable = _hygiene_pass
        elif name == RetrofitPass.TAG_DOMAIN:
            runnable = _tag_domain_pass
        elif name == RetrofitPass.SPANS:
            runnable = _spans_pass
        elif name == RetrofitPass.REPAIR:
            runnable = _repair_pass
        elif name == RetrofitPass.RENDITION_HYGIENE:
            runnable = _rendition_hygiene_pass
        else:
            runnable = _readability_hygiene_pass
        result = await runnable(store, runner, ids, workers=pool_size, stop_event=stop_event)
        outcome.passes[name] = result
        if result.stopped_reason is not None:
            _LOG.warning(
                "retrofit_pass_stopped",
                pass_name=name,
                reason=result.stopped_reason,
                entries_scanned=result.entries_scanned,
                skipped=[p for p in RetrofitPass.ALL if p in selected and p not in outcome.passes],
            )
            break
    _LOG.info(
        "retrofit_complete",
        entries=len(ids),
        workers=pool_size,
        cost_usd=round(outcome.cost_usd, 6),
        calls=outcome.calls,
        stopped_reason=outcome.stopped_reason,
        **outcome.counts(),
    )
    return outcome


# --------------------------------------------------------------------------------------
# Pass 1 — classify_kind
# --------------------------------------------------------------------------------------


#: One residue item: ``(lexeme_id, headword, gloss snippet)``.
type _Residue = tuple[str, str, str | None]


def _residue_snippet(entry: Lexeme) -> str | None:
    """Return one short gloss to disambiguate a residue term for the batched classifier.

    Args:
        entry: The entry whose kind the rules could not decide.

    Returns:
        The canonical gloss of the entry's first sense, truncated to
        :data:`EVIDENCE_SNIPPET_CHARS`, or ``None`` when the entry has no sense at all.
    """
    for _, sense, _ in entry.iter_senses():
        gloss = sense.canonical_gloss().strip()
        if gloss:
            return gloss[:EVIDENCE_SNIPPET_CHARS]
    return None


def _apply_kind(entry: Lexeme, kind: LexemeKind) -> bool:
    """Set an entry's kind, keeping the ``proper_noun`` block consistent.

    ``Lexeme`` requires the block exactly when the kind is ``proper_noun``, and the batch
    contract deliberately does not carry an entity type (it would be null nearly every
    time), so a promotion to proper noun gets ``other`` for a later pass to refine.

    Returns:
        Whether anything changed.
    """
    if entry.kind is kind:
        return False
    entry.kind = kind
    if kind is LexemeKind.PROPER_NOUN:
        entry.proper_noun = entry.proper_noun or ProperNounInfo(entity_type=EntityType.OTHER)
    else:
        entry.proper_noun = None
    if kind is LexemeKind.FUNCTION_WORD:
        entry.is_stopword = True
    return True


async def _classify_kind_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Classify every entry's kind, by rule where possible and by model otherwise.

    Two pooled phases. The first decides what the rules can, under each entry's lock, and
    collects the residue; the second sends the residue to the model 50 terms at a time and
    writes each verdict back under its own entry's lock. The residue is sorted before it
    is batched, so the same store produces the same batches — and therefore the same
    prompts and the same cache keys — whatever order the workers finished phase one in.
    """
    tally = _Tally(RetrofitPass.CLASSIFY_KIND)
    residue: list[_Residue] = []
    residue_lock = asyncio.Lock()

    async def decide(lexeme_id: str) -> None:
        undecided: _Residue | None = None
        changed = False
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            # An entry carrying the marker has been decided by this pass before (D-21).
            # One written by migration does not carry it, so its placeholder kind is
            # revisited here — with evidence the migration may not have had.
            if entry is None or _has_run(entry, StageName.CLASSIFY_KIND):
                return
            kind = classify_kind_deterministic(entry.headword, evidence=entry_evidence(entry))
            if kind is None:
                undecided = (lexeme_id, entry.headword, _residue_snippet(entry))
            else:
                changed = _apply_kind(entry, kind)
                entry.add_provenance(_marker(StageName.CLASSIFY_KIND))
                store.write(entry)
        if undecided is not None:
            async with residue_lock:
                residue.append(undecided)
            await tally.entry(metrics={"residue": 1.0})
            return
        await tally.entry(items_changed=1 if changed else 0, metrics={"deterministic": 1.0})

    await _drive(ids, decide, tally, workers=workers, stop_event=stop_event)

    residue.sort(key=lambda item: item[0])
    batches = [
        tuple(residue[start : start + KIND_BATCH_SIZE])
        for start in range(0, len(residue), KIND_BATCH_SIZE)
    ]

    async def classify(batch: tuple[_Residue, ...]) -> None:
        await _classify_kind_batch(store, runner, batch, tally)

    await _drive(batches, classify, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    scanned = result.entries_scanned
    decided_by_rule = result.metrics.setdefault("deterministic", 0.0)
    result.metrics.setdefault("residue", 0.0)
    result.metrics["deterministic_ratio"] = decided_by_rule / scanned if scanned else 0.0
    _LOG.info(
        "classify_kind_pass",
        scanned=scanned,
        deterministic=decided_by_rule,
        residue=len(residue),
        deterministic_ratio=round(result.metrics["deterministic_ratio"], 4),
        stopped_reason=result.stopped_reason,
    )
    return result


async def _classify_kind_batch(
    store: LexemeStore,
    runner: StageRunner,
    batch: Sequence[_Residue],
    tally: _Tally,
) -> None:
    """Classify one batch of ambiguous headwords and write the verdicts back.

    This is the one place a pass cannot hold the lock across its model call: the call
    decides 50 entries at once. Each verdict is applied read-modify-write inside that
    entry's own lock instead, so the write is still never based on a read taken outside
    the lock.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            stage=StageName.CLASSIFY_KIND,
            output_type=DraftKindBatch,
            instructions=prompts.CLASSIFY_KIND_INSTRUCTIONS,
            prompt=prompts.build_classify_kind_prompt(
                [(headword, snippet) for _, headword, snippet in batch]
            ),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("classify_kind_batch_failed", size=len(batch), error=str(exc))
        return

    await tally.call(stage_result.cost_usd)
    verdicts = {v.term.strip().lower(): v.kind for v in stage_result.output.verdicts}
    for lexeme_id, headword, _ in batch:
        kind = verdicts.get(headword.strip().lower())
        if kind is None:
            continue
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                continue
            changed = _apply_kind(entry, kind)
            entry.add_provenance(stage_result.provenance)
            store.write(entry)
        # The entry was already counted as scanned in phase one; this only records the
        # change the model's verdict made to it.
        await tally.entry(scanned=False, items_changed=1 if changed else 0)


# --------------------------------------------------------------------------------------
# Pass 2 — hygiene
# --------------------------------------------------------------------------------------
#
# Instructions and the output contract for step (c) live here, not in prompts.py /
# contracts.py: those files are being edited concurrently by other work on this branch,
# and keeping this pass self-contained means it never conflicts with that work. Nothing
# outside this module depends on the names below; move them once that settles.

_ARTIFACT_STOPLIST = frozenset(
    {
        "descriptor",
        "descriptive term",
        "descriptive adjective",
        "descriptive word",
        "term",
        "word",
        "thing",
        "adjective",
        "noun",
        "verb",
        "transitive verb",
        "intransitive verb",
        "action verb",
        "concept",
        "general term",
        "stock with",
        "fill with characters",
    }
)
_MAX_ARTIFACT_RELATION_WORDS = 4
_SENTENCE_PUNCTUATION = re.compile(r"[.!?;:]")

#: ``[ \t]``, not ``\s``, for the leading whitespace: ``\s`` matches newlines too, which
#: would let a heading or bullet match swallow a preceding blank line into itself.
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_MD_BULLET = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_MD_ITALIC = re.compile(
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", re.DOTALL
)
_MD_CODE = re.compile(r"`+([^`]+?)`+")
_MD_WHITESPACE = re.compile(r"[ \t]{2,}")

#: Instructions for step (c), the one nano call this pass makes. Kept short and
#: byte-stable so it caches like every other stage's instructions do.
HYGIENE_REWRITE_INSTRUCTIONS = """\
Rewrite each definition so it does NOT begin with the headword and does not name the \
headword at all; keep the meaning exactly; one sentence; dictionary style; plain prose.

Answer every definition you are given, identified by the number it was listed under."""


class _DraftGlossRewrite(BaseModel):
    """One rewritten gloss for a headword-initial offender."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    sense_ref: Annotated[int, Field(ge=1)]
    gloss: Annotated[str, Field(min_length=3, max_length=400)]


class _DraftGlossRewriteBatch(BaseModel):
    """Rewrites for every headword-initial gloss of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftGlossRewrite], Field(min_length=1)]


def _strip_markdown(text: str) -> str:
    """Strip common markdown markers from a piece of canonical prose.

    Deterministic and free: headings, bold/italic emphasis, inline code spans, and list
    bullets are removed; the underlying words are kept as plain text.

    Args:
        text: The prose to clean.

    Returns:
        The text with markdown markers removed and whitespace tidied.
    """
    stripped = _MD_HEADING.sub("", text)
    stripped = _MD_BULLET.sub("", stripped)
    stripped = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), stripped)
    stripped = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), stripped)
    stripped = _MD_CODE.sub(lambda m: m.group(1), stripped)
    stripped = _MD_WHITESPACE.sub(" ", stripped)
    return stripped.strip()


def _canonical_renditions[T](renditions: Renditions[T]) -> list[Rendition[T]]:
    """Return every rendition at the canonical ``(neutral, plain)`` key.

    :meth:`Renditions.canonical` returns only the first match, but a sense may hold
    several canonical *examples* — their uniqueness key includes the example text, so
    more than one can share the ``(neutral, plain)`` key — so this walks the whole set.

    Args:
        renditions: The rendition set to search.

    Returns:
        Every rendition whose key is :data:`~opengloss_generator.schema.CANONICAL_KEY`.
    """
    return [r for r in renditions if r.key == CANONICAL_KEY]


def _strip_entry_markdown(entry: Lexeme) -> int:
    """Strip markdown from every canonical prose field of an entry, in place.

    Covers the canonical gloss and examples of every non-retired sense, plus the
    entry's encyclopedia section and lexical explanation. A canonical example whose
    text changes has its span cleared: markdown removal shifts character offsets, and
    the ``spans`` pass — which runs after this one in :attr:`RetrofitPass.ALL` — finds
    the headword again for free.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many canonical renditions actually changed.
    """
    changed = 0
    for _, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        for rendition in _canonical_renditions(sense.gloss):
            new_text = _strip_markdown(rendition.content)
            if new_text != rendition.content:
                rendition.content = new_text
                changed += 1
        for rendition in _canonical_renditions(sense.examples):
            example = rendition.content
            new_text = _strip_markdown(example.text)
            if new_text != example.text:
                example.text = new_text
                example.span = None
                changed += 1
    for rendition in _canonical_renditions(entry.encyclopedia):
        new_text = _strip_markdown(rendition.content)
        if new_text != rendition.content:
            rendition.content = new_text
            changed += 1
    for rendition in _canonical_renditions(entry.lexical_explanation):
        new_text = _strip_markdown(rendition.content)
        if new_text != rendition.content:
            rendition.content = new_text
            changed += 1
    return changed


def _is_artifact_relation(term: str) -> bool:
    """Return whether a relation target looks like a migration artifact, not a word.

    Args:
        term: The relation target's surface form, exactly as stored.

    Returns:
        Whether the term is in the stoplist, has more than
        :data:`_MAX_ARTIFACT_RELATION_WORDS` words, or contains sentence punctuation.
    """
    normalized = " ".join(term.split()).strip().lower()
    if normalized in _ARTIFACT_STOPLIST:
        return True
    if len(normalized.split()) > _MAX_ARTIFACT_RELATION_WORDS:
        return True
    return bool(_SENTENCE_PUNCTUATION.search(term))


def _drop_artifact_relations(entry: Lexeme) -> int:
    """Drop every relation whose target is an artifact, logging what was dropped.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many relations were dropped across the whole entry.
    """
    dropped: list[str] = []
    for _, sense, _ in entry.iter_senses():
        if sense.retired or not sense.relations:
            continue
        kept = [r for r in sense.relations if not _is_artifact_relation(r.target.term)]
        if len(kept) != len(sense.relations):
            dropped.extend(r.target.term for r in sense.relations if r not in kept)
            sense.relations = kept
    if dropped:
        _LOG.debug("hygiene_dropped_relations", headword=entry.headword, targets=dropped)
    return len(dropped)


def _build_hygiene_rewrite_prompt(headword: str, glosses: Sequence[tuple[str, str]]) -> str:
    """Return the volatile half of the gloss-rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        glosses: ``(label, offending gloss)`` per sense to rewrite, in the order the
            model should answer. The label is human-readable context only; the model
            refers to a gloss by its position in this list.

    Returns:
        The per-call prompt body.
    """
    listed = "\n".join(f"  {i + 1}. [{label}] {gloss}" for i, (label, gloss) in enumerate(glosses))
    return f"Headword: {headword}\nDefinitions ({len(glosses)}):\n{listed}"


def _note_provenance(base: Provenance, note: str) -> Provenance:
    """Return a zero-cost copy of a stage's provenance record, carrying ``note``.

    The real cost and token counts of the call are recorded once, on the entry's
    generic call marker (see :func:`_rewrite_glosses`); this copy exists only so each
    rewritten sense's superseded gloss is individually retrievable, without inflating a
    naive sum of ``cost_usd`` over the entry's provenance table.

    Args:
        base: The call's own provenance record.
        note: The superseded gloss text to preserve.

    Returns:
        A copy of ``base`` with ``note`` set and cost/token fields zeroed.
    """
    return base.model_copy(
        update={
            "note": note,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "attempts": 0,
        }
    )


def _clear_weak_domains(entry: Lexeme) -> int:
    """Clear every sense's weak domain tag so the ``tag_domain`` pass re-tags it.

    A tag is weak when it is its root's ``.general`` catch-all *and* was assigned under
    an older taxonomy version (D-44), or when it came from
    :data:`~opengloss_generator.taxonomy.LEGACY_DOMAIN_MAP` rather than a real
    ``tag_domain`` verdict — ``domain_hint`` is set, and the entry carries no
    ``tag_domain`` provenance record at all. A sense whose ``domain`` is already
    ``None`` needs nothing done to it; that state already gets it re-tagged.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many senses had their domain cleared.
    """
    legacy_mapped_only = not _has_run(entry, StageName.TAG_DOMAIN)
    # A ``.general`` verdict made with the *current* leaf set on the menu is the
    # tagger's considered answer, not a weak tag; re-clearing it every sweep re-billed
    # ~11.5K senses per run on the core (CORE-DIARY iteration 6). Only a verdict that
    # predates the current taxonomy version is cleared for a retag.
    stale_taxonomy = not _tagged_under_current_taxonomy(entry)
    cleared = 0
    for _, sense, _ in entry.iter_senses():
        if sense.retired or sense.domain is None:
            continue
        legacy_mapped = legacy_mapped_only and sense.domain_hint is not None
        if (is_general(sense.domain) and stale_taxonomy) or legacy_mapped:
            sense.domain = None
            cleared += 1
    return cleared


def _taxonomy_version_note() -> str:
    """Return the provenance note stamped on a ``tag_domain`` verdict."""
    return f"taxonomy_version={TAXONOMY_VERSION}"


def _tagged_under_current_taxonomy(entry: Lexeme) -> bool:
    """Return whether the entry's latest ``tag_domain`` verdict used the current taxonomy."""
    return any(
        p.stage is StageName.TAG_DOMAIN and p.note == _taxonomy_version_note()
        for p in entry.provenance.values()
    )


async def _rewrite_glosses(
    entry: Lexeme,
    offenders: Sequence[tuple[Sense, str]],
    runner: StageRunner,
    tally: _Tally,
) -> int:
    """Ask the model to rewrite one entry's headword-initial glosses.

    Args:
        entry: The entry whose offending senses need rewriting, mutated in place.
        offenders: ``(sense, label)`` for every offending sense, in the order the model
            was shown them.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.

    Returns:
        How many glosses were actually rewritten.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            stage=StageName.HYGIENE,
            output_type=_DraftGlossRewriteBatch,
            instructions=HYGIENE_REWRITE_INSTRUCTIONS,
            prompt=_build_hygiene_rewrite_prompt(
                entry.headword, [(label, sense.canonical_gloss()) for sense, label in offenders]
            ),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("hygiene_rewrite_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so a residue the model did
    # not usefully answer is not re-billed on the next sweep — the same convention
    # ``_span_fallback`` and ``_tag_entry`` use for their own model calls.
    entry.add_provenance(stage_result.provenance)

    rewritten = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.sense_ref - 1
        if not 0 <= position < len(offenders):
            continue
        sense, _ = offenders[position]
        canonical = sense.gloss.canonical()
        if canonical is None:
            continue
        old_text = canonical.content
        canonical.content = drafted.gloss
        canonical.provenance_id = entry.add_provenance(
            _note_provenance(stage_result.provenance, old_text)
        )
        rewritten += 1
    return rewritten


async def _clean_entry(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float]]:
    """Run the four hygiene steps over one entry, in place.

    Args:
        entry: The entry to clean, mutated in place.
        runner: The stage runner, used only by step (c).
        tally: The pass tally, for step (c)'s call and cost.

    Returns:
        ``(items changed, per-step metric increments)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    stripped = _strip_entry_markdown(entry)
    dropped = _drop_artifact_relations(entry)

    # Proper-noun definitions legitimately name their entity ("The Congo River is a
    # major central African river…" — WordNet does the same), so step (c) skips
    # them entirely rather than fight the check with a tighter prompt; steps (a),
    # (b), and (d) still run. See CORE-DIARY iteration 2 and D-30.
    offenders = (
        []
        if entry.kind is LexemeKind.PROPER_NOUN
        else [
            (sense, f"{pos_entry.pos.value} {sense.index}")
            for pos_entry, sense, _ in entry.iter_senses()
            if not sense.retired and is_headword_initial(sense.canonical_gloss(), entry.headword)
        ]
    )
    rewritten = 0
    if offenders and not _has_run(entry, StageName.HYGIENE):
        rewritten = await _rewrite_glosses(entry, offenders, runner, tally)

    cleared = _clear_weak_domains(entry)
    metrics = {
        "markdown_stripped": float(stripped),
        "artifacts_dropped": float(dropped),
        "glosses_rewritten": float(rewritten),
        "domains_cleared": float(cleared),
    }
    return stripped + dropped + rewritten + cleared, metrics


async def _hygiene_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Run the four hygiene steps over every entry; see the module docstring for order.

    Args:
        store: The store to clean. Each entry is read, cleaned — including step (c)'s
            model call — and written inside one hold of its own lock.
        runner: The stage runner, used only by step (c).
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` whose ``metrics`` carry per-step counts.
    """
    tally = _Tally(RetrofitPass.HYGIENE)

    async def clean(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics = await _clean_entry(entry, runner, tally)
            if changed:
                store.write(entry)
        await tally.entry(items_changed=changed, metrics=metrics)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    for name in ("markdown_stripped", "artifacts_dropped", "glosses_rewritten", "domains_cleared"):
        result.metrics.setdefault(name, 0.0)
    result.metrics["calls"] = float(result.calls)
    result.metrics["cost"] = result.cost_usd
    return result


# --------------------------------------------------------------------------------------
# Pass 3 — tag_domain
# --------------------------------------------------------------------------------------


async def _tag_domain_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Tag every untagged sense with a controlled domain, one call per entry.

    Args:
        store: The store to tag. Each entry is read, tagged and written inside one hold
            of its own lock, the model call included.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size — this is the pass the worker count matters most for, since
            after ``hygiene`` nearly every entry needs a call.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` for the pass.
    """
    tally = _Tally(RetrofitPass.TAG_DOMAIN)

    async def tag(lexeme_id: str) -> None:
        tagged = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            # Only senses the migration could not map. A sense that already has a tag is
            # not sent, so a second sweep over a tagged store costs nothing.
            untagged = [
                (sense, f"{pos_entry.pos.value} {sense.index}")
                for pos_entry, sense, _ in entry.iter_senses()
                if sense.domain is None and not sense.retired
            ]
            if untagged:
                tagged = await _tag_entry(entry, untagged, runner, tally)
                if tagged:
                    store.write(entry)
        await tally.entry(items_changed=tagged)

    await _drive(ids, tag, tally, workers=workers, stop_event=stop_event)
    return tally.result


async def _tag_entry(
    entry: Lexeme,
    untagged: Sequence[tuple[Sense, str]],
    runner: StageRunner,
    tally: _Tally,
) -> int:
    """Ask for and apply the domain tags of one entry's untagged senses.

    Returns:
        How many senses gained a tag.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    view = [(label, sense.canonical_gloss()) for sense, label in untagged]
    try:
        stage_result = await runner.run(
            stage=StageName.TAG_DOMAIN,
            output_type=DraftDomainTags,
            instructions=prompts.TAG_DOMAIN_INSTRUCTIONS,
            prompt=prompts.build_tag_domain_prompt(entry.headword, view),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("tag_domain_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(
        stage_result.provenance.model_copy(update={"note": _taxonomy_version_note()})
    )
    tagged = 0
    for drafted in stage_result.output.tags:
        position = drafted.sense_ref - 1
        if not 0 <= position < len(untagged):
            continue
        sense = untagged[position][0]
        if sense.domain is not None:
            continue
        sense.domain = drafted.domain
        sense.secondary_domains = list(drafted.secondary_domains)
        tagged += 1
    return tagged


# --------------------------------------------------------------------------------------
# Pass 4 — spans
# --------------------------------------------------------------------------------------


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to try when locating the headword in an example."""
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


def _place_spans_free(entry: Lexeme) -> tuple[int, list[tuple[POSEntry, Example]]]:
    """Place what :func:`~opengloss_generator.spans.find_span` can, for free, in place.

    Args:
        entry: The entry whose examples are placed, mutated in place.

    Returns:
        ``(examples placed, the residue the free finder could not place)``.
    """
    placed = 0
    residue: list[tuple[POSEntry, Example]] = []
    for pos_entry in entry.pos_entries:
        forms = _forms_for(entry, pos_entry)
        for sense in pos_entry.senses:
            for rendition in sense.examples:
                example = rendition.content
                if example.span is not None:
                    continue
                span = spans.find_span(example.text, entry.headword, forms)
                if span is None:
                    residue.append((pos_entry, example))
                else:
                    example.span = span
                    placed += 1
    return placed, residue


async def _spans_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Place every example's headword span, free first and by model for the residue.

    Args:
        store: The store to place spans in. Each entry is read, placed and written inside
            one hold of its own lock, the fallback call included.
        runner: The stage runner, used only for the residue.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` whose ``metrics`` split free placements from model ones.
    """
    tally = _Tally(RetrofitPass.SPANS)

    async def place(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            placed, residue = _place_spans_free(entry)
            found_free = placed
            if residue and not _has_run(entry, StageName.SPANS):
                placed += await _span_fallback(entry, residue, runner, tally)
            if placed:
                store.write(entry)
        await tally.entry(items_changed=placed, metrics={"deterministic": float(found_free)})

    await _drive(ids, place, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    result.metrics.setdefault("deterministic", 0.0)
    result.metrics["by_model"] = float(result.items_changed) - result.metrics["deterministic"]
    return result


async def _span_fallback(
    entry: Lexeme,
    residue: Sequence[tuple[POSEntry, Example]],
    runner: StageRunner,
    tally: _Tally,
) -> int:
    """Ask the model to place the examples the free finder could not.

    Returns:
        How many examples the model placed.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    by_pos: dict[str, list[Example]] = {}
    forms_by_pos: dict[str, list[str]] = {}
    for pos_entry, example in residue:
        key = pos_entry.pos.value
        by_pos.setdefault(key, []).append(example)
        forms_by_pos[key] = _forms_for(entry, pos_entry)

    placed = 0
    for key, examples in by_pos.items():
        for start in range(0, len(examples), SPAN_BATCH_SIZE):
            batch = examples[start : start + SPAN_BATCH_SIZE]
            try:
                stage_result = await runner.run(
                    stage=StageName.SPANS,
                    output_type=DraftSpanBatch,
                    instructions=prompts.SPANS_INSTRUCTIONS,
                    prompt=prompts.build_spans_prompt(
                        entry.headword, forms_by_pos[key], [e.text for e in batch]
                    ),
                    prompt_version=PROMPT_VERSION,
                )
            except BudgetExceededError:
                raise
            except GenerationError as exc:
                _LOG.warning("span_fallback_failed", headword=entry.headword, error=str(exc))
                continue
            await tally.call(stage_result.cost_usd)
            entry.add_provenance(stage_result.provenance)
            for drafted in stage_result.output.spans:
                position = drafted.example_ref - 1
                if not 0 <= position < len(batch):
                    continue
                example = batch[position]
                if example.span is not None:
                    continue
                if not 0 <= drafted.start < drafted.end <= len(example.text):
                    continue
                example.span = (drafted.start, drafted.end)
                placed += 1
    return placed


# --------------------------------------------------------------------------------------
# Pass 5 — repair
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract for step (b) live here, not in prompts.py /
# contracts.py, for the same reason hygiene's step (c) does: one small, self-contained
# call site with no other dependents has no reason to grow either of those modules.


def _normalized_gloss(text: str) -> str:
    """Return the duplicate-detection key for a canonical gloss.

    Case- and whitespace-insensitive, and a single trailing period is ignored, so "A
    cat." and "a cat" collapse to the same key.

    Args:
        text: The canonical gloss text.

    Returns:
        The normalised key.
    """
    collapsed = " ".join(text.split()).strip().lower()
    return collapsed[:-1] if collapsed.endswith(".") else collapsed


def _retire_duplicate_senses(entry: Lexeme) -> int:
    """Retire the later of any two non-retired senses with an identical canonical gloss.

    ``entry.iter_senses()`` walks part-of-speech entries in document order and, within
    each, senses in index order, so "later" here already means "higher index; if across
    parts of speech, the one in the later part-of-speech entry" — exactly the rule this
    pass is required to follow. Nothing is ever deleted or renumbered (D-1): a duplicate
    is marked ``retired=True`` and stays in place.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many senses were newly retired.
    """
    seen: set[str] = set()
    retired = 0
    for _, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        key = _normalized_gloss(sense.canonical_gloss())
        if key in seen:
            sense.retired = True
            retired += 1
        else:
            seen.add(key)
    return retired


#: Sentinel written to the ``note`` of this pass's one model call, so a later sweep can
#: tell "repair already tried this entry's missing examples" apart from an ordinary
#: hygiene gloss-rewrite record — both carry ``stage=StageName.HYGIENE`` because this
#: step reuses hygiene's model policy rather than adding a stage for one call site (see
#: the module docstring).
_REPAIR_EXAMPLES_NOTE = "repair:examples_generated"


def _repair_marker(needing_sense_ids: list[str]) -> str:
    """Return the marker note for one attempt over a specific set of example-less senses.

    Keyed on the sense set (D-47), not on the entry: a later pass that empties a
    *different* sense must earn a fresh attempt. A per-entry boolean left 54 senses on the
    core without examples after ``sense_hygiene`` removed theirs (QA-DIARY, close-out).
    """
    digest = hashlib.sha256(",".join(sorted(needing_sense_ids)).encode()).hexdigest()[:16]
    return f"{_REPAIR_EXAMPLES_NOTE}:{digest}"


def _has_repaired(entry: Lexeme, needing_sense_ids: list[str]) -> bool:
    """Return whether this exact set of example-less senses was already attempted."""
    marker = _repair_marker(needing_sense_ids)
    return any(record.note == marker for record in entry.provenance.values())


#: Instructions for this pass's one nano call. Kept short and byte-stable so it caches
#: like every other stage's instructions do. The example rules echo
#: :data:`~opengloss_generator.prompts.SENSES_INSTRUCTIONS`'s own — natural sentences a
#: person would actually write, not corpus-style or academic-register constructions —
#: since these are the same canonical examples that stage would have written the first
#: time, just late.
REPAIR_EXAMPLES_INSTRUCTIONS = """\
Write one or two example sentences for each numbered sense below.

Sentences must be natural — the kind of thing a person would actually write or say, not \
a corpus-style or academic-register construction, and never framed as something \
"researchers" study. Each sentence must contain the headword itself or a natural \
inflected form of it (plural, past tense, -ing form, and so on), must fit the numbered \
sense's meaning, and must not equally fit another sense of the same headword — the \
entry's other senses are listed for context so you can tell them apart. Plain prose, at \
most 20 words per sentence.

Answer every numbered sense you are given, identified by the number it was listed under."""


class _DraftRepairExample(BaseModel):
    """One or two example sentences written for a sense that had none."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    sense_ref: Annotated[int, Field(ge=1)]
    sentences: Annotated[list[str], Field(min_length=1, max_length=2)]


class _DraftRepairExamples(BaseModel):
    """Examples for every example-less sense of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    examples: Annotated[list[_DraftRepairExample], Field(min_length=1)]


def _build_repair_examples_prompt(
    headword: str,
    needing: Sequence[tuple[str, str]],
    other_senses: Sequence[tuple[str, str]],
) -> str:
    """Return the volatile half of the repair pass's example-generation prompt.

    Args:
        headword: The lexeme's surface form.
        needing: ``(label, canonical gloss)`` for every sense that needs at least one
            canonical example, in the order the model should answer — ``sense_ref`` in
            the reply is a 1-based position into this list.
        other_senses: ``(label, canonical gloss)`` for the entry's other non-retired
            senses, shown for context only so the model can tell them apart from the
            ones it is writing for. Never referenced by ``sense_ref``.

    Returns:
        The per-call prompt body.
    """
    listed = "\n".join(f"  {i + 1}. [{label}] {gloss}" for i, (label, gloss) in enumerate(needing))
    lines = [f"Headword: {headword}", f"Senses needing examples ({len(needing)}):", listed]
    if other_senses:
        context = "\n".join(f"  - [{label}] {gloss}" for label, gloss in other_senses)
        lines.append(f"Other senses of this headword, for context only:\n{context}")
    return "\n".join(lines)


async def _generate_examples(
    entry: Lexeme,
    needing: Sequence[tuple[Sense, str, POSEntry]],
    other_senses: Sequence[tuple[str, str]],
    runner: StageRunner,
    tally: _Tally,
    *,
    needing_ids: list[str],
) -> int:
    """Ask the model for canonical examples of one entry's example-less senses.

    Args:
        entry: The entry whose senses need examples, mutated in place.
        needing: ``(sense, label, pos_entry)`` for every sense to write for, in the order
            the model was shown them.
        other_senses: ``(label, gloss)`` for the entry's other senses, shown for context.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.
        needing_ids: Sense ids of ``needing``, in order; keys the idempotence marker.

    Returns:
        How many example sentences were actually added.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano, low effort) rather than adding a
            # stage for this one call site; see the module docstring and
            # ``_REPAIR_EXAMPLES_NOTE``.
            stage=StageName.HYGIENE,
            output_type=_DraftRepairExamples,
            instructions=REPAIR_EXAMPLES_INSTRUCTIONS,
            prompt=_build_repair_examples_prompt(
                entry.headword,
                [(label, sense.canonical_gloss()) for sense, label, _ in needing],
                other_senses,
            ),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("repair_examples_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so a sense the model did
    # not usefully answer for is not re-billed on the next sweep — the same convention
    # ``_rewrite_glosses`` and ``_span_fallback`` use for their own model calls.
    entry.add_provenance(
        stage_result.provenance.model_copy(update={"note": _repair_marker(needing_ids)})
    )

    added = 0
    for drafted in stage_result.output.examples:
        position = drafted.sense_ref - 1
        if not 0 <= position < len(needing):
            continue
        sense, _, pos_entry = needing[position]
        forms = _forms_for(entry, pos_entry)
        for raw_sentence in drafted.sentences:
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            # find_span is the same free finder the spans pass uses; a sentence it
            # cannot place is still kept, with span=None — the spans pass's own model
            # fallback gets another try at it on the next sweep.
            span = spans.find_span(sentence, entry.headword, forms)
            try:
                sense.examples.add(canonical_rendition(Example(text=sentence, span=span)))
            except ValueError:
                continue  # a sentence identical to one already added for this sense
            added += 1
    return added


async def _repair_entry(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float]]:
    """Run both repair steps over one entry, in place.

    Args:
        entry: The entry to repair, mutated in place.
        runner: The stage runner, used only by step (b).
        tally: The pass tally, for step (b)'s call and cost.

    Returns:
        ``(items changed, per-step metric increments)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    retired = _retire_duplicate_senses(entry)

    needing: list[tuple[Sense, str, POSEntry]] = []
    other_senses: list[tuple[str, str]] = []
    for pos_entry, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        label = f"{pos_entry.pos.value} {sense.index}"
        if _canonical_renditions(sense.examples):
            other_senses.append((label, sense.canonical_gloss()))
        else:
            needing.append((sense, label, pos_entry))

    added = 0
    needing_ids = [sense_id(entry.lexeme_id, pe.pos.value, sn.index) for sn, _, pe in needing]
    if needing and not _has_repaired(entry, needing_ids):
        added = await _generate_examples(
            entry, needing, other_senses, runner, tally, needing_ids=needing_ids
        )

    metrics = {
        "senses_retired": float(retired),
        "examples_added": float(added),
        "entries_needing_examples": 1.0 if needing else 0.0,
    }
    return retired + added, metrics


async def _repair_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Retire exact-duplicate senses and fill in canonical examples that are missing.

    Args:
        store: The store to repair. Each entry is read, repaired — including step (b)'s
            model call when it is due — and written inside one hold of its own lock.
        runner: The stage runner, used only by step (b).
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` whose ``metrics`` carry ``senses_retired``,
        ``examples_added`` and ``entries_needing_examples``.
    """
    tally = _Tally(RetrofitPass.REPAIR)

    async def repair(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics = await _repair_entry(entry, runner, tally)
            if changed:
                store.write(entry)
        await tally.entry(items_changed=changed, metrics=metrics)

    await _drive(ids, repair, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    for name in ("senses_retired", "examples_added", "entries_needing_examples"):
        result.metrics.setdefault(name, 0.0)
    result.metrics["calls"] = float(result.calls)
    result.metrics["cost"] = result.cost_usd
    return result


# --------------------------------------------------------------------------------------
# Pass 6 — rendition_hygiene
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract for this pass live here, not in prompts.py /
# contracts.py, for the same reason hygiene's step (c) and repair's step (b) do: one
# small, self-contained call site with no other dependents has no reason to grow either
# of those modules.


#: Prefix of the sentinel written to the ``note`` of this pass's model call for an entry,
#: so a later sweep can tell "rendition_hygiene already answered for this entry" apart
#: from a hygiene gloss-rewrite record or a repair example record — all three carry
#: ``stage=StageName.HYGIENE``, because all three reuse that stage's model policy rather
#: than adding a stage for one call site (see the module docstring). What follows the
#: prefix is the offending set's digest and the attempt count (:func:`_hygiene_attempt_due`).
_RENDITION_HYGIENE_PREFIX = "rendition_hygiene"


#: Instructions for this pass's one nano call. Kept short and byte-stable so it caches
#: like every other stage's instructions do. The worked opening is on a headword the
#: pass will never actually be asked about, so it can be copied as a shape rather than
#: as text: told only "do not begin with the headword", a nano model reliably answers
#: with "The word X means …", which is the same defect one clause later.
RENDITION_HYGIENE_INSTRUCTIONS = """\
Rewrite each definition below so that it does NOT begin with the headword and does not \
name the headword at all. Start with the meaning itself, the way a dictionary does: for \
the headword "ban", write "An order from someone in charge that stops people doing \
something.", not "A ban is an order to stop." and not "The word ban means an order."

Change nothing else. Each definition is labelled with the reading level and the register \
it was written for, and the rewrite must stay at that reading level and in that \
register: the same meaning, the same audience, sentences of the same length, the same \
vocabulary. Plain prose, no markdown.

Answer every definition you are given, identified by the number it was listed under."""


class _DraftRenditionRewrite(BaseModel):
    """One rewritten gloss rendition for a headword-initial offender."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rendition_ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=1000)]


class _DraftRenditionRewriteBatch(BaseModel):
    """Rewrites for every headword-initial gloss rendition of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftRenditionRewrite], Field(min_length=1)]


@dataclass(slots=True)
class _RenditionOffender:
    """One offending stored gloss rendition, and what the pass needs to track it by.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is applied.
        label: The ``level/register`` label it is listed to the model under, so the
            rewrite is held at the audience the rendition was written for.
        rendition_id: Its derived identifier, which is what the entry's marker digest is
            taken over (D-47) — stable across sweeps, unlike the rendition's text or its
            position in the offending list.
    """

    rendition: Rendition[str]
    label: str
    rendition_id: str


def _headword_initial_renditions(entry: Lexeme) -> list[_RenditionOffender]:
    """Return every stored non-canonical gloss rendition that opens with the headword.

    Canonical glosses are the ``hygiene`` pass's business, not this one's, so they are
    skipped here whatever they look like — an entry whose canonical still offends is one
    ``hygiene`` has yet to reach. Proper nouns contribute nothing at all: their
    definitions legitimately name their own entity (D-30).

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_RenditionOffender` per offender, in document order — which is the
        order the model is shown them in and refers to them by.
    """
    if entry.kind is LexemeKind.PROPER_NOUN:
        return []
    offenders: list[_RenditionOffender] = []
    for _, sense, sid in entry.iter_senses():
        if sense.retired:
            continue
        offenders.extend(
            _RenditionOffender(
                rendition=rendition,
                label=f"{rendition.reading_level.value}/{rendition.style.value}",
                rendition_id=rendition_id(
                    sid, rendition.reading_level.value, rendition.style.value
                ),
            )
            for rendition in sense.gloss
            if not rendition.is_canonical and is_headword_initial(rendition.content, entry.headword)
        )
    return offenders


def _build_rendition_hygiene_prompt(headword: str, offenders: Sequence[_RenditionOffender]) -> str:
    """Return the volatile half of this pass's rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        offenders: The renditions to rewrite, in the order the model should answer them.
            ``rendition_ref`` in the reply is a 1-based position into this list; the
            ``level/register`` label is what tells the model which audience to hold.

    Returns:
        The per-call prompt body.
    """
    listed = "\n".join(
        f"  {i + 1}. [{offender.label}] {offender.rendition.content}"
        for i, offender in enumerate(offenders)
    )
    return f"Headword: {headword}\nDefinitions ({len(offenders)}):\n{listed}"


def _remeasure_rendition(rendition: Rendition[str], headword: str) -> None:
    """Re-score a rewritten rendition and reconcile its headword-initial flag.

    The rewrite changed the text, so the stored Flesch-Kincaid grade is about a sentence
    that no longer exists; it is recomputed here rather than left stale, on the same
    terms ``workflows/enrich.py`` computed it on (the headword scored as one syllable).
    The flag is set or cleared to match what the new text actually is, so
    ``audit.py``'s two counts — the flag count and the recomputed one — cannot drift.

    Args:
        rendition: The rewritten rendition, mutated in place.
        headword: The entry's surface form.
    """
    assessment = rendition.assessment or Assessment()
    assessment.readability_grade = round(
        flesch_kincaid_grade(rendition.content, ignore=(headword,)), 2
    )
    if is_headword_initial(rendition.content, headword):
        assessment.flag(QAFlag.OG_HEADWORD_INITIAL)
    elif QAFlag.OG_HEADWORD_INITIAL in assessment.qa_flags:
        assessment.qa_flags.remove(QAFlag.OG_HEADWORD_INITIAL)
    rendition.assessment = assessment


async def _rewrite_renditions(
    entry: Lexeme,
    offenders: Sequence[_RenditionOffender],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> int:
    """Ask the model to rewrite one entry's headword-initial gloss renditions.

    Args:
        entry: The entry whose renditions need rewriting, mutated in place.
        offenders: The renditions to rewrite, in the order the model was shown them.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.
        marker_note: The offending-set marker to stamp on the call's provenance record,
            from :func:`_hygiene_attempt_due`.

    Returns:
        How many renditions were actually rewritten.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano, low effort) rather than adding a
            # stage for this one call site; see the module docstring.
            stage=StageName.HYGIENE,
            output_type=_DraftRenditionRewriteBatch,
            instructions=RENDITION_HYGIENE_INSTRUCTIONS,
            prompt=_build_rendition_hygiene_prompt(entry.headword, offenders),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("rendition_hygiene_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so a rendition the model
    # did not usefully rewrite is not re-billed for the same offending set on the next
    # sweep — the same convention every other model call in this module uses.
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    rewritten = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.rendition_ref - 1
        if not 0 <= position < len(offenders):
            continue
        rendition = offenders[position].rendition
        text = _strip_markdown(drafted.text)
        if not text or text == rendition.content:
            continue
        old_text = rendition.content
        rendition.content = text
        rendition.provenance_id = entry.add_provenance(
            _note_provenance(stage_result.provenance, old_text)
        )
        _remeasure_rendition(rendition, entry.headword)
        rewritten += 1
    return rewritten


def _reconcile_near_copy(rendition: Rendition[str], canonical_text: str) -> bool:
    """Set or clear ``OG_NEAR_COPY`` on one stored gloss rendition to match its text now.

    Free (D-59): comparing two already-stored strings costs nothing, so unlike
    :func:`_rewrite_renditions`'s offenders this is not gated by an attempt marker — there
    is no cost to bound, and the verdict can simply be recomputed on every sweep.

    Args:
        rendition: The stored register rendition, mutated in place.
        canonical_text: The sense's canonical gloss to compare it against.

    Returns:
        Whether the flag's presence changed (added or removed), so the caller can tell
        whether the entry needs writing.
    """
    assessment = rendition.assessment or Assessment()
    was_flagged = QAFlag.OG_NEAR_COPY in assessment.qa_flags
    now_near_copy = is_near_copy(rendition.content, canonical_text)
    if now_near_copy:
        assessment.flag(QAFlag.OG_NEAR_COPY)
    elif was_flagged:
        assessment.qa_flags.remove(QAFlag.OG_NEAR_COPY)
    rendition.assessment = assessment
    return now_near_copy != was_flagged


def _flag_near_copy_renditions(entry: Lexeme) -> int:
    """Reconcile ``OG_NEAR_COPY`` on every stored non-``plain`` gloss rendition (D-59).

    No proper-noun exemption, unlike :func:`_headword_initial_renditions`: a proper noun's
    formal and slang registers still have to read differently from each other.

    Args:
        entry: The entry to inspect, mutated in place.

    Returns:
        How many renditions had their flag added or removed.
    """
    changed = 0
    for _, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        canonical = sense.canonical_gloss()
        if not canonical:
            continue
        for rendition in sense.gloss:
            if rendition.style is Register.PLAIN:
                continue
            if _reconcile_near_copy(rendition, canonical):
                changed += 1
    return changed


async def _clean_renditions(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float], bool]:
    """Rewrite one entry's headword-initial gloss renditions, and flag its near-copies.

    Args:
        entry: The entry to clean, mutated in place.
        runner: The stage runner.
        tally: The pass tally, for the call and its cost.

    Returns:
        ``(items changed, metric increments, whether the entry needs writing)``. The
        first element covers both the rewrite and the flag-only step, so the pass's
        ``entries_changed``/``items_changed`` totals count a near-copy flag flipping as a
        change even on an entry with no headword-initial offender at all. The third
        element is not the first one's truth value: a call that succeeded but rewrote
        nothing still leaves the entry's idempotence marker to be persisted, or the next
        sweep pays for the same answer again — and a near-copy flag changing is reason
        enough to write on its own, even with no rewrite call at all.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    offenders = _headword_initial_renditions(entry)
    marker_note = _hygiene_attempt_due(
        entry, _RENDITION_HYGIENE_PREFIX, [offender.rendition_id for offender in offenders]
    )
    rewritten = 0
    called = False
    if marker_note is not None:
        rewritten = await _rewrite_renditions(entry, offenders, runner, tally, marker_note)
        called = True
    still_initial = len(_headword_initial_renditions(entry)) if offenders else 0
    near_copy_flagged = _flag_near_copy_renditions(entry)
    metrics = {
        "renditions_rewritten": float(rewritten),
        "still_initial": float(still_initial),
        "near_copy_flagged": float(near_copy_flagged),
    }
    return (
        rewritten + near_copy_flagged,
        metrics,
        called or bool(rewritten) or bool(near_copy_flagged),
    )


async def _rendition_hygiene_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Rewrite stored gloss renditions that begin by naming their own headword.

    Also flags, for free, every stored non-``plain`` gloss rendition that is a near-copy
    of its sense's canonical gloss (D-59) — see :func:`_flag_near_copy_renditions`.

    Args:
        store: The store to clean. Each entry is read, cleaned — including its one model
            call when it is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` whose ``metrics`` carry ``renditions_rewritten``,
        ``still_initial`` and ``near_copy_flagged`` alongside the usual ``calls`` and
        ``cost``.
    """
    tally = _Tally(RetrofitPass.RENDITION_HYGIENE)

    async def clean(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics, needs_write = await _clean_renditions(entry, runner, tally)
            if needs_write:
                store.write(entry)
        await tally.entry(items_changed=changed, metrics=metrics)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    for name in ("renditions_rewritten", "still_initial", "near_copy_flagged"):
        result.metrics.setdefault(name, 0.0)
    result.metrics["calls"] = float(result.calls)
    result.metrics["cost"] = result.cost_usd
    return result


# --------------------------------------------------------------------------------------
# Pass 7 — readability_hygiene
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract for this pass live here, not in prompts.py /
# contracts.py, for the same reason every other single-call-site pass in this module does:
# one small, self-contained call site with no other dependents has no reason to grow
# either of those modules. Where this pass differs from its siblings is that its
# instructions are not hand-written prose of their own: they are assembled from the exact
# reading-level and field-meaning text of :data:`~opengloss_generator.prompts.
# RENDITIONS_INSTRUCTIONS`, sliced out at import time rather than retyped, so the
# constraints a rewrite must satisfy can never drift from the constraints the original
# rendition was written against.

#: Prefix of the sentinel written to the ``note`` of this pass's model call(s) for one
#: entry, so a later sweep can tell "readability_hygiene already answered for this entry"
#: apart from an ordinary ``enrich.py`` rendition-generation record under the same
#: ``StageName.RENDITIONS`` stage (which carries no note at all) — see the module
#: docstring. What follows the prefix is the flagged set's digest and the attempt count,
#: exactly as ``rendition_hygiene``'s marker carries them (:func:`_hygiene_attempt_due`).
_READABILITY_HYGIENE_PREFIX = "readability_hygiene"


def _extract_instructions_block(source: str, start_marker: str, end_marker: str) -> str:
    """Return the substring of ``source`` between two markers, trimmed.

    Used only at import time, to lift a section of
    :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` verbatim into this pass's
    own instructions rather than retyping it — see the module docstring.

    Args:
        source: The text to slice.
        start_marker: The literal text the wanted section begins with.
        end_marker: The literal text that follows the wanted section.

    Returns:
        The text from ``start_marker`` up to (not including) ``end_marker``, stripped of
        surrounding whitespace.

    Raises:
        ValueError: If either marker is not found in ``source`` — a signal that
            ``RENDITIONS_INSTRUCTIONS`` changed shape and this pass's instructions need
            re-slicing, not that the entries being processed are at fault.
    """
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end].strip()


#: The "READING LEVELS." section of ``RENDITIONS_INSTRUCTIONS`` verbatim: the same hard
#: per-level constraints (sentence length, vocabulary, no formulas, and so on) the
#: original rendition was written against, so a rewrite is held to the identical bar.
_READABILITY_LEVEL_CONSTRAINTS = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "READING LEVELS.", "\n\nREGISTERS."
)

#: The "WHAT THE FIELD MEANS FOR YOUR OUTPUT." section of ``RENDITIONS_INSTRUCTIONS``
#: verbatim: what a ``gloss``, ``examples``, ``encyclopedia`` or ``explanation`` rewrite is
#: for, so this pass's mixed batch of field kinds is treated the way each one demands.
_READABILITY_FIELD_MEANINGS = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS,
    "WHAT THE FIELD MEANS FOR YOUR OUTPUT.",
    "\n\nWORKED EXAMPLE.",
)

#: The one-sentence headword-initial rule of ``RENDITIONS_INSTRUCTIONS`` verbatim. Without
#: it this pass rewrites a hard gloss into "A ban is an order to stop." — a *readability*
#: win that is a headword-initial regression, which is exactly how the core's
#: headword-initial gloss renditions rose from 4,546 to 6,480 (D-47).
_READABILITY_HEADWORD_RULE = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "Never begin a definition rendition", "\n\nFormatting,"
)

#: Instructions for this pass's call(s). The three blocks above are reused verbatim rather
#: than restated (see the module docstring); only the framing paragraphs and the answer
#: format around them are new.
READABILITY_HYGIENE_INSTRUCTIONS = f"""\
Rewrite each numbered passage below so it measures inside the Flesch-Kincaid grade band \
of the reading level named for it. Each one was already written for that level and \
register; a reader too advanced for it flagged it as too hard. Keep its meaning and its \
register exactly as they are — do not change the audience, the facts, or the degree of \
formality — and simplify only what the band requires: shorter sentences, fewer clauses, \
plainer words.

{_READABILITY_HEADWORD_RULE}

{_READABILITY_LEVEL_CONSTRAINTS}

{_READABILITY_FIELD_MEANINGS}

Formatting: plain prose, no markdown. No bold, no italics, no backticks, no bullets, no \
headings, no numbered lists, and no asterisks or underscores used for emphasis.

Each passage below is labelled with its field, its reading level and register, and the \
Flesch-Kincaid grade it measured at against the limit its level allows.

Answer every passage you are given, identified by the number it was listed under."""

#: Word budget for one call's worth of source text. Roughly 3,000 words of source
#: (mostly encyclopedia passages, which run 350-1,600 words each at the higher levels)
#: keeps one call's prompt and expected rewrite comfortably inside the ``RENDITIONS``
#: policy's token budget; an entry with more flagged text than this is split across
#: however many calls it takes to keep every call under the budget.
_READABILITY_WORD_BUDGET = 3000


class _DraftReadabilityRewrite(BaseModel):
    """One rewritten passage for a readability-band offender."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=6000)]


class _DraftReadabilityRewriteBatch(BaseModel):
    """Rewrites for every readability-band offender in one call, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftReadabilityRewrite], Field(min_length=1)]


@dataclass(slots=True)
class _ReadabilityOffender:
    """One stored rendition flagged ``OG_READABILITY_MISS`` and how to rewrite it.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is applied.
        field_name: Which field this rendition belongs to — ``gloss``, ``examples``,
            ``encyclopedia`` or ``explanation``, the same vocabulary
            :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` itself uses —
            shown to the model so it treats each kind of passage correctly.
        pos_entry: The owning part-of-speech entry, needed only for ``examples`` to
            re-find the headword's span in a rewritten sentence; ``None`` for every other
            field.
        rendition_id: Its derived identifier, which is what the entry's marker digest is
            taken over (D-47). Example renditions have no unique keyed id — several
            examples may share one ``(level, register)`` — so theirs carries the
            offender's position within its sense's example list as well.
    """

    rendition: Rendition[Any]
    field_name: str
    pos_entry: POSEntry | None
    rendition_id: str
    #: The owning sense, for ``examples`` only: a rewrite must not land on text a sibling
    #: rendition at the same level and register already holds, or the entry fails
    #: validation on its next read (seen after the 2026-09-03 outage: ``calendaring``).
    sense: Sense | None = None


def _is_readability_miss(rendition: Rendition[Any]) -> bool:
    """Return whether a rendition's assessment carries ``OG_READABILITY_MISS``."""
    return rendition.assessment is not None and (
        QAFlag.OG_READABILITY_MISS in rendition.assessment.qa_flags
    )


def _readability_offenders(entry: Lexeme) -> list[_ReadabilityOffender]:
    """Return every rendition of this entry still flagged ``OG_READABILITY_MISS``.

    Covers every text-bearing field a rendition can live on: sense glosses and examples of
    every non-retired sense, plus the entry's encyclopedia and lexical-explanation
    sections. Unlike ``rendition_hygiene``, nothing here is exempt by kind — a readability
    miss is a property of the text, not of what the headword happens to be.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_ReadabilityOffender` per flagged rendition, in document order — the
        order the model is shown them in and refers to them by within each call.
    """

    def rid(owner: str, rendition: Rendition[Any], position: str = "") -> str:
        return rendition_id(owner, rendition.reading_level.value, rendition.style.value) + position

    offenders: list[_ReadabilityOffender] = []
    for pos_entry, sense, sid in entry.iter_senses():
        if sense.retired:
            continue
        offenders.extend(
            _ReadabilityOffender(
                rendition=r, field_name="gloss", pos_entry=None, rendition_id=rid(sid, r)
            )
            for r in sense.gloss
            if _is_readability_miss(r)
        )
        offenders.extend(
            _ReadabilityOffender(
                rendition=r,
                field_name="examples",
                pos_entry=pos_entry,
                rendition_id=rid(sid, r, f"[{index}]"),
                sense=sense,
            )
            for index, r in enumerate(sense.examples)
            if _is_readability_miss(r)
        )
    encyclopedia_owner = encyclopedia_owner_id(entry.lexeme_id)
    offenders.extend(
        _ReadabilityOffender(
            rendition=r,
            field_name="encyclopedia",
            pos_entry=None,
            rendition_id=rid(encyclopedia_owner, r),
        )
        for r in entry.encyclopedia
        if _is_readability_miss(r)
    )
    explanation_owner = explanation_owner_id(entry.lexeme_id)
    offenders.extend(
        _ReadabilityOffender(
            rendition=r,
            field_name="explanation",
            pos_entry=None,
            rendition_id=rid(explanation_owner, r),
        )
        for r in entry.lexical_explanation
        if _is_readability_miss(r)
    )
    return offenders


def _offender_text(rendition: Rendition[Any]) -> str:
    """Return the plain text of one rendition.

    Handles both content shapes a rendition here can carry: a bare string (gloss,
    encyclopedia, lexical explanation) or an :class:`~opengloss_generator.schema.Example`.
    """
    content = rendition.content
    return content.text if isinstance(content, Example) else content


def _offender_grade(rendition: Rendition[Any], headword: str) -> float:
    """Return a rendition's Flesch-Kincaid grade, from its assessment or freshly measured.

    Args:
        rendition: The rendition to grade.
        headword: The entry's surface form, scored as one syllable (a definition cannot
            avoid its own headword).

    Returns:
        The stored ``readability_grade`` when present, since that is the exact figure the
        band-miss decision was made on; otherwise a fresh measurement of the stored text.
    """
    assessment = rendition.assessment
    if assessment is not None and assessment.readability_grade is not None:
        return assessment.readability_grade
    return flesch_kincaid_grade(_offender_text(rendition), ignore=(headword,))


def _chunk_by_word_budget(
    offenders: Sequence[_ReadabilityOffender],
) -> list[list[_ReadabilityOffender]]:
    """Split offenders into chunks whose source text stays under the word budget.

    Args:
        offenders: The entry's flagged renditions, in document order.

    Returns:
        One or more chunks, each a contiguous slice of ``offenders`` in the order given.
        A single offender whose own text exceeds the budget is still chunked alone rather
        than raising, since one over-budget passage is still one call, not zero.
    """
    chunks: list[list[_ReadabilityOffender]] = []
    current: list[_ReadabilityOffender] = []
    current_words = 0
    for offender in offenders:
        words = word_count(_offender_text(offender.rendition))
        if current and current_words + words > _READABILITY_WORD_BUDGET:
            chunks.append(current)
            current = []
            current_words = 0
        current.append(offender)
        current_words += words
    if current:
        chunks.append(current)
    return chunks


def _readability_feedback_for(chunk: Sequence[_ReadabilityOffender], headword: str) -> str:
    """Return the readability-feedback block for one call's worth of offenders.

    Reuses :func:`~opengloss_generator.prompts.build_readability_feedback` verbatim rather
    than restating its wording: one note per distinct reading level present in ``chunk``,
    carrying that level's worst measured grade, exactly as the generation-time retry
    builds its own feedback (docs/CORE-DIARY.md).

    Args:
        chunk: The offenders going into one call.
        headword: The entry's surface form.

    Returns:
        The feedback text.
    """
    worst: dict[ReadingLevel, float] = {}
    for offender in chunk:
        level = offender.rendition.reading_level
        grade = _offender_grade(offender.rendition, headword)
        worst[level] = max(worst.get(level, grade), grade)
    misses = [(level, grade, grade_band(level)[1]) for level, grade in worst.items()]
    return prompts.build_readability_feedback(misses)


def _build_readability_hygiene_prompt(headword: str, chunk: Sequence[_ReadabilityOffender]) -> str:
    """Return one call's prompt body: every offender in ``chunk``, plus feedback.

    Args:
        headword: The lexeme's surface form.
        chunk: The offenders to list, in the order the model should answer them — ``ref``
            in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = []
    for i, offender in enumerate(chunk):
        rendition = offender.rendition
        # Collapsed to one line: an encyclopedia passage may hold internal newlines, and
        # the listing format below is one item per line.
        text = " ".join(_offender_text(rendition).split())
        grade = _offender_grade(rendition, headword)
        limit = grade_band(rendition.reading_level)[1]
        lines.append(
            f"  {i + 1}. [{offender.field_name} {rendition.reading_level.value}/"
            f"{rendition.style.value}] (measured FK {grade:.1f}, band limit {limit:.1f}) {text}"
        )
    listed = "\n".join(lines)
    feedback = _readability_feedback_for(chunk, headword)
    return f"Headword: {headword}\nPassages ({len(chunk)}):\n{listed}\n\n{feedback}"


def _reintroduces_headword_initial(
    entry: Lexeme, offender: _ReadabilityOffender, new_text: str
) -> bool:
    """Return whether a readability rewrite of a gloss would open with the headword.

    This pass simplifies prose, and the simplest form of a definition is the one a
    dictionary must not use: "A ban is an order to stop." A rewrite that trades the
    readability defect for the headword-initial one is not an improvement, so it is
    refused here and the old text — and its readability flag — kept, exactly as a rewrite
    that reads no easier is (D-47). Only gloss renditions are held to the rule:
    :data:`~opengloss_generator.hygiene.is_headword_initial` describes a *definition*, an
    example sentence may open with its headword, and a proper noun's definition
    legitimately names its own entity (D-30).

    Args:
        entry: The entry the rendition belongs to.
        offender: The offending rendition and its field.
        new_text: The markdown-stripped rewrite under consideration.

    Returns:
        Whether the rewrite must be refused. A refusal is logged, since it is the one
        outcome here that a prompt change could reduce.
    """
    if offender.field_name != "gloss" or entry.kind is LexemeKind.PROPER_NOUN:
        return False
    if not is_headword_initial(new_text, entry.headword):
        return False
    _LOG.info(
        "readability_rewrite_rejected_headword_initial",
        headword=entry.headword,
        rendition=offender.rendition_id,
    )
    return True


def _example_collides(offender: _ReadabilityOffender, new_text: str) -> bool:
    """Return whether ``new_text`` duplicates a sibling example's uniqueness key."""
    if offender.sense is None:
        return False
    own = offender.rendition
    return any(
        other is not own
        and other.reading_level is own.reading_level
        and other.style is own.style
        and other.content.text == new_text
        for other in offender.sense.examples
    )


def _apply_readability_rewrite(
    entry: Lexeme,
    offender: _ReadabilityOffender,
    drafted_text: str,
    tolerance: float,
    base_provenance: Provenance,
) -> bool:
    """Apply one drafted rewrite to its offending rendition, if it is actually better.

    The rewrite is markdown-stripped and, for an example, re-checked for the headword's
    span before it is even considered: a rewrite ``find_span`` cannot place has lost the
    headword and is discarded outright, whatever its grade. A gloss rewrite that begins by
    naming the headword is discarded on the same terms
    (:func:`_reintroduces_headword_initial`). Otherwise the lower of the old
    and new Flesch-Kincaid grades wins — a rewrite that reads *harder* than what is already
    stored is not applied. Whichever text ends up stored is re-scored and
    :data:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS` is set or cleared to
    match, so the flag never lags behind the text it describes.

    Args:
        entry: The entry the rendition belongs to, mutated in place.
        offender: The offending rendition and the context needed to rewrite it.
        drafted_text: The model's proposed rewrite, before markdown stripping.
        tolerance: How far above its band's upper bound a grade may still count as
            in-band, from :attr:`~opengloss_generator.config.ReadabilityConfig.tolerance`.
        base_provenance: The call's own provenance record, used to build the zero-cost
            note record that keeps the superseded text.

    Returns:
        Whether the rendition's content actually changed.
    """
    rendition = offender.rendition
    headword = entry.headword
    new_text = _strip_markdown(drafted_text)
    old_text = _offender_text(rendition)
    old_grade = _offender_grade(rendition, headword)
    adopted = False
    if (
        new_text
        and new_text != old_text
        and not _reintroduces_headword_initial(entry, offender, new_text)
    ):
        new_grade = flesch_kincaid_grade(new_text, ignore=(headword,))
        if offender.field_name == "examples":
            forms = _forms_for(entry, offender.pos_entry) if offender.pos_entry else []
            span = spans.find_span(new_text, headword, forms)
            content = rendition.content
            if (
                span is not None
                and new_grade < old_grade
                and isinstance(content, Example)
                and not _example_collides(offender, new_text)
            ):
                content.text = new_text
                content.span = span
                adopted = True
        elif new_grade < old_grade:
            rendition.content = new_text
            adopted = True

    if adopted:
        rendition.provenance_id = entry.add_provenance(_note_provenance(base_provenance, old_text))
        final_grade = flesch_kincaid_grade(_offender_text(rendition), ignore=(headword,))
    else:
        final_grade = old_grade

    assessment = rendition.assessment or Assessment()
    assessment.readability_grade = round(final_grade, 2)
    if final_grade > grade_band(rendition.reading_level)[1] + tolerance:
        assessment.flag(QAFlag.OG_READABILITY_MISS)
    elif QAFlag.OG_READABILITY_MISS in assessment.qa_flags:
        assessment.qa_flags.remove(QAFlag.OG_READABILITY_MISS)
    rendition.assessment = assessment
    return adopted


async def _rewrite_readability_chunk(
    entry: Lexeme,
    chunk: Sequence[_ReadabilityOffender],
    runner: StageRunner,
    tally: _Tally,
    *,
    tolerance: float,
    marker_note: str,
) -> int:
    """Rewrite one call's worth of offenders and apply what comes back.

    Args:
        entry: The entry being rewritten, mutated in place.
        chunk: The offenders going into this one call.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.
        tolerance: How far above its band a grade may still count as in-band.
        marker_note: The flagged-set marker to stamp on the call's provenance record,
            from :func:`_hygiene_attempt_due`.

    Returns:
        How many renditions in ``chunk`` were actually rewritten.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): this is prose for an audience, not a
            # structural verdict, so it gets the model the renditions were written by
            # rather than hygiene's nano. See the module docstring.
            stage=StageName.RENDITIONS,
            output_type=_DraftReadabilityRewriteBatch,
            instructions=READABILITY_HYGIENE_INSTRUCTIONS,
            prompt=_build_readability_hygiene_prompt(entry.headword, chunk),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("readability_hygiene_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so an offender the model did
    # not usefully rewrite is not re-billed for the same flagged set on the next sweep —
    # the same convention every other model call in this module uses.
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    rewritten = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(chunk):
            continue
        if _apply_readability_rewrite(
            entry, chunk[position], drafted.text, tolerance, stage_result.provenance
        ):
            rewritten += 1
    return rewritten


async def _rewrite_readability(
    entry: Lexeme,
    offenders: Sequence[_ReadabilityOffender],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> int:
    """Rewrite every flagged rendition of one entry, one call per word-budget chunk.

    Args:
        entry: The entry whose offenders need rewriting, mutated in place.
        offenders: Every flagged rendition, in document order.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.
        marker_note: The flagged-set marker every one of this entry's calls is stamped
            with; they all answer for the same set, so they carry the same marker.

    Returns:
        How many renditions were actually rewritten, across every chunk.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    tolerance = runner.config.readability.tolerance
    rewritten = 0
    for chunk in _chunk_by_word_budget(offenders):
        rewritten += await _rewrite_readability_chunk(
            entry, chunk, runner, tally, tolerance=tolerance, marker_note=marker_note
        )
    return rewritten


async def _clean_readability(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float], bool]:
    """Rewrite one entry's readability-band offenders, in place.

    Args:
        entry: The entry to clean, mutated in place.
        runner: The stage runner.
        tally: The pass tally, for the call(s) and their cost.

    Returns:
        ``(items changed, metric increments, whether the entry needs writing)``. The
        third element is not the first one's truth value: a call that succeeded but
        rewrote nothing still leaves the entry's idempotence marker to be persisted, or
        the next sweep pays for the same answer again.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything already applied is discarded, since a chunk already applied
            before the stop stays applied (the entry is written with whatever it holds).
    """
    offenders = _readability_offenders(entry)
    marker_note = _hygiene_attempt_due(
        entry, _READABILITY_HYGIENE_PREFIX, [offender.rendition_id for offender in offenders]
    )
    rewritten = 0
    called = False
    if marker_note is not None:
        rewritten = await _rewrite_readability(entry, offenders, runner, tally, marker_note)
        called = True

    now_in_band = sum(1 for offender in offenders if not _is_readability_miss(offender.rendition))
    still_out_of_band = len(offenders) - now_in_band
    metrics = {
        "renditions_rewritten": float(rewritten),
        "now_in_band": float(now_in_band),
        "still_out_of_band": float(still_out_of_band),
    }
    return rewritten, metrics, called or bool(rewritten)


async def _readability_hygiene_pass(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> PassResult:
    """Rewrite stored renditions that still miss their readability band.

    Args:
        store: The store to clean. Each entry is read, cleaned — including its call(s)
            when they are due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop.

    Returns:
        A :class:`PassResult` whose ``metrics`` carry ``renditions_rewritten``,
        ``now_in_band`` and ``still_out_of_band`` alongside the usual ``calls`` and
        ``cost``.
    """
    tally = _Tally(RetrofitPass.READABILITY_HYGIENE)

    async def clean(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics, needs_write = await _clean_readability(entry, runner, tally)
            if needs_write:
                store.write(entry)
        await tally.entry(items_changed=changed, metrics=metrics)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)

    result = tally.result
    for name in ("renditions_rewritten", "now_in_band", "still_out_of_band"):
        result.metrics.setdefault(name, 0.0)
    result.metrics["calls"] = float(result.calls)
    result.metrics["cost"] = result.cost_usd
    return result
