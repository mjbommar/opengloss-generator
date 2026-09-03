"""Workflow 2 — grow the graph by walking it.

Sample a node, look at what it points at, keep the targets that have no entry, filter
them hard, generate the survivors, and push *their* targets onto the frontier. The walk
is bounded by max new entries, budget, wall-clock, and depth, and stops on whichever
binds first (FR-2.6).

The filter ordering is the economics of the whole workflow: the free filters in
``filters.py`` run first and the LLM classifier only sees what survives them.

The ``domain-deficit`` sampling strategy (``SamplingStrategy.DOMAIN_DEFICIT``) is free:
it only reads ``domain`` tags already on the store's senses and calls
``taxonomy.deficit_table`` to rank taxonomy roots, so choosing a seed this way makes no
model call and costs nothing on its own.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator import prompts
from opengloss_generator.contracts import FrontierJudgement, RelatedTerms
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.filters import FilterChain
from opengloss_generator.identity import slugify
from opengloss_generator.log import get_logger
from opengloss_generator.schema import Lexeme, LexemeKind, StageName
from opengloss_generator.taxonomy import ROOTS, DomainTag, deficit_table, root_of
from opengloss_generator.workflows.generate import EntrySpec, generate_entry

if TYPE_CHECKING:
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["SamplingStrategy", "WalkOutcome", "WalkSpec", "sample_seeds", "walk_graph"]

_LOG = get_logger(__name__)

_CLASSIFIER_BATCH = 40

#: A walk does not, by default, look for further neighbours from a function word or an
#: affix (docs/SCHEMA-V3.md § 6): neither has a useful neighbourhood to expand into.
_NO_USEFUL_NEIGHBOURHOOD = frozenset({LexemeKind.FUNCTION_WORD, LexemeKind.AFFIX})


def _default_expand_kinds() -> set[LexemeKind]:
    """Return every :class:`LexemeKind` a walk expands from by default."""
    return set(LexemeKind) - _NO_USEFUL_NEIGHBOURHOOD


class SamplingStrategy:
    """Seed-selection strategies for a walk."""

    RANDOM = "random"
    LEAST_CONNECTED = "least-connected"
    EXPLICIT = "explicit"
    DOMAIN_DEFICIT = "domain-deficit"


@dataclass(slots=True)
class WalkSpec:
    """Bounds and behaviour for one walk."""

    seeds: list[str] = field(default_factory=list)
    strategy: str = SamplingStrategy.RANDOM
    seed_count: int = 1
    max_new_entries: int = 10
    max_depth: int = 2
    max_seconds: float | None = None
    propose_related: bool = False
    related_limit: int = 10
    use_classifier: bool = True
    rng_seed: int | None = None
    expand_kinds: set[LexemeKind] | None = field(default_factory=_default_expand_kinds)


@dataclass(slots=True)
class WalkOutcome:
    """What a walk produced."""

    generated: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0
    calls: int = 0
    stop_reason: str = "frontier_exhausted"
    rejection_counts: dict[str, int] = field(default_factory=dict)
    domain_deficit: dict[str, float] = field(default_factory=dict)

    @property
    def generated_count(self) -> int:
        """Return how many new entries the walk produced."""
        return len(self.generated)


def sample_seeds(store: LexemeStore, spec: WalkSpec) -> list[str]:
    """Choose the entries a walk starts from.

    Args:
        store: The store to sample from.
        spec: The walk specification.

    Returns:
        Lexeme ids to start from. Empty if the store is empty and no explicit seed
        was given.
    """
    if spec.seeds:
        return [slugify(seed) for seed in spec.seeds]

    if spec.strategy == SamplingStrategy.DOMAIN_DEFICIT:
        seeds, _ = _domain_deficit_seeds(store, spec)
        return seeds

    ids = sorted(store.iter_ids())
    if not ids:
        return []

    rng = random.Random(spec.rng_seed)  # noqa: S311 - sampling, not crypto
    if spec.strategy == SamplingStrategy.LEAST_CONNECTED:
        scored = []
        for lexeme_id in ids:
            entry = store.read(lexeme_id)
            if entry is not None:
                scored.append((len(entry.relation_targets()), lexeme_id))
        scored.sort()
        return [lexeme_id for _, lexeme_id in scored[: spec.seed_count]]
    return rng.sample(ids, k=min(spec.seed_count, len(ids)))


def _domain_deficit_seeds(store: LexemeStore, spec: WalkSpec) -> tuple[list[str], dict[str, float]]:
    """Pick seeds from the taxonomy roots furthest below their target share.

    This strategy is free: it only reads ``domain`` tags already on the store's senses
    and calls :func:`~opengloss_generator.taxonomy.deficit_table`; it makes no model
    call. Senses whose ``domain`` is ``None`` count toward an "untagged" bucket that is
    reported back but never sampled from, since there is no root to seed.

    Args:
        store: The store to sample from.
        spec: The walk specification; ``seed_count`` bounds how many ids come back and
            ``rng_seed`` makes the tie-breaking reproducible.

    Returns:
        ``(seed lexeme ids, deficit table)``. The table has one entry per taxonomy root
        plus ``"untagged"`` for the count of untagged senses found.
    """
    counts: Counter[DomainTag] = Counter()
    untagged = 0
    ids_by_root: dict[str, set[str]] = {root: set() for root in ROOTS}
    for entry in store.iter_entries():
        for _, sense, _ in entry.iter_senses():
            if sense.retired:
                continue
            if sense.domain is None:
                untagged += 1
                continue
            counts[sense.domain] += 1
            ids_by_root[root_of(sense.domain)].add(entry.lexeme_id)

    table = deficit_table(counts)
    rng = random.Random(spec.rng_seed)  # noqa: S311 - sampling, not crypto
    grouped: dict[float, list[str]] = {}
    for root, deficit in table.items():
        grouped.setdefault(deficit, []).append(root)

    seeds: list[str] = []
    seen: set[str] = set()
    for deficit in sorted(grouped, reverse=True):
        # Roots tied on deficit are equally good candidates, so which one goes first is
        # decided by the same seeded rng that samples within a root, not by enum order.
        group = grouped[deficit]
        rng.shuffle(group)
        for root in group:
            if len(seeds) >= spec.seed_count:
                break
            candidates = sorted(ids_by_root[root] - seen)
            if not candidates:
                continue
            need = spec.seed_count - len(seeds)
            picked = candidates if len(candidates) <= need else rng.sample(candidates, k=need)
            seeds.extend(picked)
            seen.update(picked)
        if len(seeds) >= spec.seed_count:
            break

    report = dict(table)
    report["untagged"] = float(untagged)
    return seeds, report


def _should_expand(entry: Lexeme, spec: WalkSpec) -> bool:
    """Return whether a walk should look for further neighbours from ``entry``.

    ``spec.expand_kinds`` of ``None`` lifts the restriction entirely; otherwise only a
    kind in that set has its relations harvested onto the frontier (docs/SCHEMA-V3.md
    § 6) — by default that excludes function words and affixes, which have no useful
    neighbourhood.
    """
    return spec.expand_kinds is None or entry.kind in spec.expand_kinds


def _select_seeds(store: LexemeStore, spec: WalkSpec) -> tuple[list[str], dict[str, float]]:
    """Return ``(seeds, domain_deficit)`` for the walk's chosen strategy.

    The deficit table is only non-empty for :attr:`SamplingStrategy.DOMAIN_DEFICIT`
    with no explicit seed; every other path reports an empty table.
    """
    if not spec.seeds and spec.strategy == SamplingStrategy.DOMAIN_DEFICIT:
        return _domain_deficit_seeds(store, spec)
    return sample_seeds(store, spec), {}


async def _seed_from_entries(
    seeds: list[str],
    *,
    spec: WalkSpec,
    store: LexemeStore,
    runner: StageRunner,
    frontier: deque[tuple[str, str, int]],
    outcome: WalkOutcome,
    visited: set[str],
) -> bool:
    """Expand the frontier from each of the walk's starting seeds.

    Returns:
        ``False`` if the budget guard stopped expansion partway through, in which case
        the walk's stop reason has been set and the caller should stop.
    """
    for seed in seeds:
        entry = store.read(seed)
        if entry is None:
            outcome.skipped[seed] = "seed_not_in_store"
            continue
        visited.add(seed)
        if not _should_expand(entry, spec):
            continue
        if not await _expand(
            entry,
            depth=1,
            spec=spec,
            store=store,
            runner=runner,
            frontier=frontier,
            outcome=outcome,
        ):
            return False
    return True


async def walk_graph(
    spec: WalkSpec,
    *,
    store: LexemeStore,
    runner: StageRunner,
) -> WalkOutcome:
    """Expand the store by walking outward from sampled seeds.

    Args:
        spec: Bounds and behaviour.
        store: The store to read from and write to.
        runner: The stage runner.

    Returns:
        A :class:`WalkOutcome` naming what was generated and why the walk stopped.
    """
    outcome = WalkOutcome()
    seeds, outcome.domain_deficit = _select_seeds(store, spec)
    if not seeds:
        outcome.stop_reason = "no_seed"
        return outcome

    deadline = (
        dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=spec.max_seconds)
        if spec.max_seconds is not None
        else None
    )
    frontier: deque[tuple[str, str, int]] = deque()
    visited: set[str] = set()

    if not await _seed_from_entries(
        seeds,
        spec=spec,
        store=store,
        runner=runner,
        frontier=frontier,
        outcome=outcome,
        visited=visited,
    ):
        return outcome

    while frontier:
        if len(outcome.generated) >= spec.max_new_entries:
            outcome.stop_reason = "max_new_entries"
            break
        if deadline is not None and dt.datetime.now(tz=dt.UTC) >= deadline:
            outcome.stop_reason = "deadline"
            break

        term, source, depth = frontier.popleft()
        lexeme_id = slugify(term)
        if lexeme_id in visited or store.exists(lexeme_id):
            outcome.skipped[term] = "already_present"
            continue
        visited.add(lexeme_id)

        try:
            result = await generate_entry(EntrySpec(headword=term, discovered_from=source), runner)
        except BudgetExceededError:
            outcome.stop_reason = "budget"
            break
        except GenerationError as exc:
            outcome.skipped[term] = f"generation_failed: {exc}"
            _LOG.warning("walk_generation_failed", term=term, error=str(exc))
            continue

        outcome.cost_usd += result.cost_usd
        outcome.calls += result.calls
        async with store.locked(result.entry.lexeme_id):
            store.write(result.entry)
        outcome.generated.append(result.entry.lexeme_id)
        _LOG.info(
            "walk_entry_generated",
            headword=term,
            discovered_from=source,
            depth=depth,
            cost_usd=round(result.cost_usd, 6),
        )

        if (
            depth < spec.max_depth
            and _should_expand(result.entry, spec)
            and not await _expand(
                result.entry,
                depth=depth + 1,
                spec=spec,
                store=store,
                runner=runner,
                frontier=frontier,
                outcome=outcome,
            )
        ):
            break

    return outcome


async def _expand(
    entry: Lexeme,
    *,
    depth: int,
    spec: WalkSpec,
    store: LexemeStore,
    runner: StageRunner,
    frontier: deque[tuple[str, str, int]],
    outcome: WalkOutcome,
) -> bool:
    """Expand the frontier from one entry, folding cost into the outcome.

    Returns:
        ``False`` if the budget guard stopped the expansion, in which case the walk's
        stop reason has been set and the caller should stop.
    """
    try:
        cost, calls = await _seed_frontier(
            entry=entry,
            spec=spec,
            store=store,
            runner=runner,
            frontier=frontier,
            outcome=outcome,
            depth=depth,
        )
    except BudgetExceededError:
        outcome.stop_reason = "budget"
        return False
    outcome.cost_usd += cost
    outcome.calls += calls
    return True


async def _seed_frontier(
    *,
    entry: Lexeme,
    spec: WalkSpec,
    store: LexemeStore,
    runner: StageRunner,
    frontier: deque[tuple[str, str, int]],
    outcome: WalkOutcome,
    depth: int,
) -> tuple[float, int]:
    """Harvest, filter, and enqueue one entry's dangling relation targets.

    Returns:
        ``(cost, call_count)`` for any model calls this made.
    """
    cost = 0.0
    calls = 0

    candidates = list(entry.relation_targets())
    if spec.propose_related:
        related = await runner.run(
            stage=StageName.FRONTIER,
            output_type=RelatedTerms,
            instructions=prompts.RELATED_TERMS_INSTRUCTIONS,
            prompt=prompts.build_related_terms_prompt(entry, spec.related_limit),
            prompt_version=prompts.PROMPT_VERSION,
        )
        cost += related.cost_usd
        calls += 1
        candidates.extend(related.output.terms)

    known = set(store.iter_ids())
    chain = FilterChain(known_ids=known, source_id=entry.lexeme_id)
    filtered = chain.run(candidates)
    for reason, count in filtered.reason_counts().items():
        outcome.rejection_counts[reason] = outcome.rejection_counts.get(reason, 0) + count

    survivors = filtered.accepted
    if survivors and spec.use_classifier:
        judged_cost, judged_calls, survivors = await _classify(survivors, runner, outcome)
        cost += judged_cost
        calls += judged_calls

    frontier.extend((term, entry.lexeme_id, depth) for term in survivors)
    _LOG.info(
        "frontier_expanded",
        source=entry.lexeme_id,
        depth=depth,
        raw=len(candidates),
        accepted=len(survivors),
        rejected=filtered.rejected_count,
    )
    return cost, calls


async def _classify(
    terms: list[str],
    runner: StageRunner,
    outcome: WalkOutcome,
) -> tuple[float, int, list[str]]:
    """Ask the model which surviving candidates are real headwords.

    Runs in batches so one prompt covers many candidates; a per-candidate call would cost
    orders of magnitude more for the same decision.

    Returns:
        ``(cost, call_count, accepted_terms)``.
    """
    cost = 0.0
    calls = 0
    accepted: list[str] = []
    for start in range(0, len(terms), _CLASSIFIER_BATCH):
        batch = terms[start : start + _CLASSIFIER_BATCH]
        try:
            judged = await runner.run(
                stage=StageName.FRONTIER,
                output_type=FrontierJudgement,
                instructions=prompts.FRONTIER_INSTRUCTIONS,
                prompt=prompts.build_frontier_prompt(batch),
                prompt_version=prompts.PROMPT_VERSION,
            )
        except GenerationError as exc:
            # A classifier failure must not silently drop the batch; keep it and let the
            # free filters be the only gate for these terms.
            _LOG.warning("frontier_classifier_failed", size=len(batch), error=str(exc))
            accepted.extend(batch)
            continue
        cost += judged.cost_usd
        calls += 1
        verdicts = {v.term.strip().lower(): v for v in judged.output.verdicts}
        for term in batch:
            verdict = verdicts.get(term.strip().lower())
            if verdict is None or verdict.is_headword:
                accepted.append(term)
            else:
                outcome.skipped[term] = f"classifier: {verdict.reason}"
    return cost, calls, accepted
