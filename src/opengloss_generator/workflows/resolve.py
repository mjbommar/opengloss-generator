"""Workflow 4 — resolve relation targets from terms to senses.

A relation is written by the sense that asserts it, pointing at a bare surface form:
``abseil:verb:0 --synonym--> "rappel"``. That is a *word* graph. Resolution turns it into
a *sense* graph by choosing which sense of ``rappel`` the link means, and recording the
choice as ``RelationTarget.sense_id`` plus a confidence.

Three properties make this affordable (``docs/SCHEMA-V3.md`` § 5):

* **Targets that are not in the store are never sent.** An unresolved target costs
  nothing and stays ``sense_id=None`` until its entry exists, so a walk in progress does
  not pay for links into vocabulary it has not generated yet.
* **One call per source entry, not per relation.** Up to 40 targets share a prompt; the
  worst case is roughly 5K tokens for a decision the cheapest model makes well.
* **Edge identity does not move.** ``edge_id`` is built from the slug of the target
  *term*, so resolving a target never renumbers an edge (``schema.Edge``).

:func:`resolve_store` sweeps at the configured worker count through
:func:`~opengloss_generator.runner.run_pool`, and each entry is read, resolved and written
inside one hold of its own lock — the model call included (D-31). Holding the lock across
the call is what makes the write safe: a read taken outside the lock and written under it
loses whatever another worker, or another process sweeping the same store, wrote in
between. Target entries are *read* without their locks, which is safe because they are
never written here and ``store.write`` swaps a complete file into place atomically.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator import prompts
from opengloss_generator.contracts import RESOLVE_BATCH_SIZE, DraftResolution
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Lexeme, StageName

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.prompts import ResolveTarget
    from opengloss_generator.schema import Relation
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["ResolveOutcome", "resolve_entry", "resolve_store"]

_LOG = get_logger(__name__)


@dataclass(slots=True)
class ResolveOutcome:
    """What a resolution pass achieved and what it cost."""

    cost_usd: float = 0.0
    calls: int = 0
    resolved: int = 0
    declined: int = 0
    absent_targets: int = 0
    entries_changed: list[str] = field(default_factory=list)
    #: Why a sweep stopped early — ``"budget"`` or ``"stopped"`` — or ``None`` if it ran
    #: to the end. Only :func:`resolve_store` sets it; a budget stop there is reported
    #: rather than raised, so the caller still gets the work already done (D-31).
    stopped_reason: str | None = None

    @property
    def changed(self) -> bool:
        """Return whether any relation gained a sense id."""
        return bool(self.resolved)

    def merge(self, other: ResolveOutcome) -> None:
        """Fold another outcome into this one."""
        self.cost_usd += other.cost_usd
        self.calls += other.calls
        self.resolved += other.resolved
        self.declined += other.declined
        self.absent_targets += other.absent_targets
        self.entries_changed.extend(other.entries_changed)


@dataclass(slots=True)
class _Pending:
    """One unresolved relation together with the target senses it may point at."""

    relation: Relation
    source_gloss: str
    candidate_ids: list[str]
    view: ResolveTarget


def _candidates(target: Lexeme) -> tuple[list[str], list[tuple[str, str]]]:
    """Return the sense inventory of a target entry as ids and a promptable view.

    Retired senses are excluded: a link into a tombstone is worse than no link.
    """
    ids: list[str] = []
    view: list[tuple[str, str]] = []
    for pos_entry, sense, sense_id in target.iter_senses():
        if sense.retired:
            continue
        ids.append(sense_id)
        view.append((f"{pos_entry.pos.value} {sense.index}", sense.canonical_gloss()))
    return ids, view


def _collect(entry: Lexeme, store: LexemeStore, outcome: ResolveOutcome) -> list[_Pending]:
    """Gather the entry's unresolved relations whose target exists in the store."""
    cache: dict[str, Lexeme | None] = {}
    pending: list[_Pending] = []
    for _, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        source_gloss = sense.canonical_gloss()
        for relation in sense.relations:
            if relation.target.sense_id is not None:
                continue
            lexeme_id = relation.target.lexeme_id
            if lexeme_id not in cache:
                cache[lexeme_id] = store.read(lexeme_id) if store.exists(lexeme_id) else None
            target = cache[lexeme_id]
            if target is None:
                outcome.absent_targets += 1
                continue
            ids, view = _candidates(target)
            if not ids:
                outcome.absent_targets += 1
                continue
            pending.append(
                _Pending(
                    relation=relation,
                    source_gloss=source_gloss,
                    candidate_ids=ids,
                    view=(relation.type.value, relation.target.term, source_gloss, view),
                )
            )
    return pending


async def resolve_entry(
    entry: Lexeme,
    store: LexemeStore,
    runner: StageRunner,
) -> ResolveOutcome:
    """Resolve one entry's relation targets to senses, in place.

    Args:
        entry: The source entry. Mutated in place.
        store: The store used to look up target entries and their sense inventories.
        runner: The stage runner.

    Returns:
        A :class:`ResolveOutcome`. ``cost_usd`` is exactly ``0.0`` when there was
        nothing to resolve — every target already resolved, or none of them in the store.

    Raises:
        BudgetExceededError: If the run's ceiling is reached mid-entry.
    """
    outcome = ResolveOutcome()
    pending = _collect(entry, store, outcome)
    if not pending:
        _LOG.info("resolve_noop", headword=entry.headword)
        return outcome

    for start in range(0, len(pending), RESOLVE_BATCH_SIZE):
        chunk = pending[start : start + RESOLVE_BATCH_SIZE]
        await _resolve_chunk(entry, chunk, runner, outcome)

    if outcome.resolved:
        outcome.entries_changed.append(entry.lexeme_id)
    return outcome


async def _resolve_chunk(
    entry: Lexeme,
    chunk: Sequence[_Pending],
    runner: StageRunner,
    outcome: ResolveOutcome,
) -> None:
    """Run one resolution call over at most 40 targets and write the answers back.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        result = await runner.run(
            stage=StageName.RESOLVE,
            output_type=DraftResolution,
            instructions=prompts.RESOLVE_INSTRUCTIONS,
            prompt=prompts.build_resolve_prompt(entry.headword, [item.view for item in chunk]),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        # A failed resolution leaves the targets unresolved, which is the state they were
        # already in; it never invalidates an entry.
        _LOG.warning("resolve_stage_failed", headword=entry.headword, error=str(exc))
        return

    outcome.cost_usd += result.cost_usd
    outcome.calls += 1
    entry.add_provenance(result.provenance)
    for drafted in result.output.resolutions:
        position = drafted.target_ref - 1
        if not 0 <= position < len(chunk):
            continue
        item = chunk[position]
        if drafted.sense_choice is None:
            outcome.declined += 1
            continue
        if drafted.sense_choice >= len(item.candidate_ids):
            _LOG.warning(
                "resolve_choice_out_of_range",
                term=item.relation.target.term,
                choice=drafted.sense_choice,
            )
            continue
        item.relation.target.sense_id = item.candidate_ids[drafted.sense_choice]
        item.relation.target.confidence = drafted.confidence
        outcome.resolved += 1


async def resolve_store(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    limit: int | None = None,
    workers: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> ResolveOutcome:
    """Resolve every entry in a store, or a named subset of it.

    Entries are swept concurrently at ``workers`` at a time, and each one is read,
    resolved and written inside one hold of its own lock — the model call included — so a
    sweep can run alongside generation, or alongside a second sweep, without either side
    losing a write.

    Args:
        store: The store to sweep.
        runner: The stage runner.
        lexeme_ids: Ids to process; defaults to every id in the store, sorted so a
            re-run visits them in the same order.
        limit: Stop after this many entries.
        workers: Pool size; defaults to the runner's configured ``concurrency.workers``.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it
            from outside to end the sweep after the entries in hand.

    Returns:
        The merged :class:`ResolveOutcome` for every entry visited, with
        ``entries_changed`` sorted so the result does not depend on completion order. A
        budget stop is reported on ``stopped_reason`` rather than raised.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    if limit is not None:
        ids = ids[:limit]
    pool_size = runner.config.concurrency.workers if workers is None else workers

    total = ResolveOutcome()
    # Every mutation of `total` happens inside this lock. `+=` on an int is atomic between
    # await points in single-threaded asyncio, but `merge` is a multi-field update called
    # from many handlers, so the discipline is made explicit rather than assumed.
    merge_lock = asyncio.Lock()

    async def handle(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            outcome = await resolve_entry(entry, store, runner)
            if outcome.changed:
                store.write(entry)
        async with merge_lock:
            total.merge(outcome)

    async def guarded(lexeme_id: str) -> None:
        try:
            await handle(lexeme_id)
        except BudgetExceededError:
            # `run_pool` stops the pool cleanly on this and swallows it, so the reason is
            # recorded here or it is lost.
            async with merge_lock:
                total.stopped_reason = total.stopped_reason or "budget"
            raise

    await run_pool(ids, guarded, workers=pool_size, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        total.stopped_reason = total.stopped_reason or "stopped"
    total.entries_changed.sort()
    _LOG.info(
        "resolve_store_complete",
        entries=len(ids),
        workers=pool_size,
        resolved=total.resolved,
        declined=total.declined,
        absent_targets=total.absent_targets,
        cost_usd=round(total.cost_usd, 6),
        stopped_reason=total.stopped_reason,
    )
    return total
