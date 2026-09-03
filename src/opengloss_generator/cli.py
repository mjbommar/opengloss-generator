"""Command-line interface.

Every command that spends money accepts ``--budget``, ``--concurrency``, and
``--dry-run``, and prints a run summary with the actual cost when it finishes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from opengloss_generator.audit import AuditReport, audit_store
from opengloss_generator.config import AppConfig, load_config
from opengloss_generator.errors import BudgetExceededError, OpenGlossError
from opengloss_generator.migrate import detect_version
from opengloss_generator.migrate import from_v2 as migrate_from_v2
from opengloss_generator.migrate import from_v13 as migrate_from_v13
from opengloss_generator.migrate import migrate as migrate_payload
from opengloss_generator.pricing import (
    PRICE_TABLE,
    PRICING_AS_OF,
    PRICING_SOURCES,
    ServiceTier,
    estimate_cost,
)
from opengloss_generator.runner import RunSession, run_pool
from opengloss_generator.schema import LexemeKind, ReadingLevel, Register, StageName
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.content_hygiene import run_content_hygiene
from opengloss_generator.workflows.enrich import (
    EnrichmentSpec,
    RenditionField,
    RenditionRequest,
    enrich_entry,
    plan_renditions,
)
from opengloss_generator.workflows.example_hygiene import run_example_hygiene
from opengloss_generator.workflows.examples import plan_examples, run_examples
from opengloss_generator.workflows.generate import EntrySpec, generate_entry
from opengloss_generator.workflows.graph_hygiene import run_graph_hygiene
from opengloss_generator.workflows.qa import QAOutcome, run_qa, stratified_sample
from opengloss_generator.workflows.qa_pairs import (
    QACallRecord,
    plan_qa_pairs,
    run_qa_pairs,
)
from opengloss_generator.workflows.relation_hygiene import run_relation_hygiene
from opengloss_generator.workflows.resolve import resolve_entry, resolve_store
from opengloss_generator.workflows.retrofit import RetrofitPass, run_retrofit
from opengloss_generator.workflows.sense_hygiene import run_sense_hygiene
from opengloss_generator.workflows.vocabulary_hygiene import run_vocabulary_hygiene
from opengloss_generator.workflows.walk import WalkSpec, walk_graph

app = typer.Typer(
    name="opengloss",
    help="Generate and enrich the OpenGloss lexical knowledge graph.",
    no_args_is_help=True,
    add_completion=False,
)

_ConfigOpt = Annotated[Path | None, typer.Option("--config", help="TOML config file.")]
_StoreOpt = Annotated[Path | None, typer.Option("--store", help="Store root directory.")]
_BudgetOpt = Annotated[float | None, typer.Option("--budget", help="Run ceiling in USD.")]
_ConcurrencyOpt = Annotated[int | None, typer.Option("--concurrency", help="Worker count.")]
_DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Plan without calling a model.")]


def _build_config(
    config_path: Path | None,
    store: Path | None,
    budget: float | None,
    concurrency: int | None,
    dry_run: bool = False,
) -> AppConfig:
    """Assemble the configuration from a file plus CLI overrides."""
    cfg = load_config(config_path, budget_usd=budget, dry_run=dry_run or None)
    if store is not None:
        cfg.store.root = store
    if concurrency is not None:
        cfg.concurrency.workers = concurrency
    return cfg


def _echo_summary(summary: dict[str, object]) -> None:
    """Print a run summary as JSON on stdout."""
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


def _parse_levels(raw: str | None) -> list[ReadingLevel]:
    """Parse a comma-separated reading-level list."""
    if not raw:
        return []
    return [ReadingLevel(token.strip()) for token in raw.split(",") if token.strip()]


def _parse_registers(raw: str | None) -> list[Register]:
    """Parse a comma-separated register list."""
    if not raw:
        return []
    return [Register(token.strip()) for token in raw.split(",") if token.strip()]


def _parse_fields(raw: str | None) -> list[RenditionField]:
    """Parse a comma-separated rendition-field list, defaulting to gloss alone."""
    tokens = [token.strip() for token in (raw or "gloss").split(",") if token.strip()]
    return [RenditionField(token) for token in tokens]


def _parse_kinds(raw: str | None) -> set[LexemeKind] | None:
    """Parse a comma-separated lexeme-kind list; ``None`` keeps the walk's default."""
    if raw is None:
        return None
    return {LexemeKind(token.strip()) for token in raw.split(",") if token.strip()}


def _read_word_list(path: Path) -> list[str]:
    """Read a batch word list from a TSV/CSV/plain-text file.

    A header row carrying a ``word`` column (comma- or tab-separated) selects that
    column for every following row; otherwise the first whitespace- or tab-separated
    token of each line is used, one word per line.

    Args:
        path: The file to read.

    Returns:
        The headwords, in file order, blank lines and empty cells dropped.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    delimiter = "\t" if "\t" in lines[0] else ("," if "," in lines[0] else None)
    if delimiter is not None:
        header = [cell.strip().lower() for cell in lines[0].split(delimiter)]
        if "word" in header:
            index = header.index("word")
            return [
                cells[index].strip()
                for line in lines[1:]
                if (cells := line.split(delimiter)) and index < len(cells) and cells[index].strip()
            ]
    words: list[str] = []
    for line in lines:
        tokens = line.split()
        if tokens:
            words.append(tokens[0])
    return words


#: A deliberately rough per-call prompt-token estimate for ``enrich --dry-run``'s batch
#: cost projection: headword, source text, and any existing renditions, before the
#: (cached) instructions. Real calls vary well outside this; the summary labels the
#: number an estimate for exactly that reason.
# Measured on pilot 2 (2026-09-02, 206 renditions calls): mean input 2,000 tokens of
# which ~87% cached, mean output ~220. Pricing at `max_tokens` overstated a sweep 30x.
_DRY_RUN_INPUT_TOKEN_ESTIMATE = 2000
_DRY_RUN_CACHED_INPUT_TOKEN_ESTIMATE = 1700
_DRY_RUN_OUTPUT_TOKEN_ESTIMATE = 250

#: Per-call token estimates for ``examples --dry-run``. Measured, not modelled, on D-53's
#: live check (2026-09-02: `river`, `argue`, `bank` — 1, 2 and 7 live senses — copied out
#: of the core store, eight sentences asked per sense). ``INPUT`` is the whole prompt and
#: ``CACHED_INPUT`` the part of it a warm provider cache served. The generation call
#: measured 2,634 / 2,659 / 2,841 input tokens of which 2,460 were the cached instruction
#: prefix, for 296-1,804 output tokens: the answer, not the prompt, is what scales with
#: sense count, which is the whole point of the stage. The sense-fit call measured 1,621
#: and 2,328 input for 404 and 1,267 output — its verdicts are a handful of integers, but
#: nano is on ``low`` reasoning under the shared HYGIENE policy and reasoning tokens are
#: billed as output — and its prefix did **not** cache on either call, so it is priced
#: uncached here. These are means over three entries and a sweep's real cost scales with
#: senses per entry, which is why the summary labels the number an estimate, exactly as
#: the enrich and qa estimates above do.
_DRY_RUN_EXAMPLES_INPUT_TOKEN_ESTIMATE = 2700
_DRY_RUN_EXAMPLES_CACHED_INPUT_TOKEN_ESTIMATE = 2400
_DRY_RUN_EXAMPLES_OUTPUT_TOKEN_ESTIMATE = 1100
_DRY_RUN_EXAMPLES_CHECK_INPUT_TOKEN_ESTIMATE = 2000
_DRY_RUN_EXAMPLES_CHECK_CACHED_INPUT_TOKEN_ESTIMATE = 0
_DRY_RUN_EXAMPLES_CHECK_OUTPUT_TOKEN_ESTIMATE = 850

#: Per-entry token estimate for ``qa --dry-run``. Measured, not modelled, on the first
#: live judge call (``vow``, 3 senses / 14 sampled renditions, 2026-09-02): 8,022 input
#: and 3,310 output. The input is three roughly equal parts — the ~2.4K-token static
#: rubric, the ~1.6K-token entry, and the ~1.5K-token JSON schema the native structured
#: output sends on every call — and the output is a verdict object carrying one record
#: per sense and one per sampled rendition, which is why it is nothing like the few
#: hundred tokens a `resolve` answer costs. The pre-measurement guess these constants
#: replaced (4,000/900) under-priced a sweep by a factor of three (D-48).
_DRY_RUN_QA_INPUT_TOKEN_ESTIMATE = 8000
_DRY_RUN_QA_OUTPUT_TOKEN_ESTIMATE = 3300

#: Per-sense token estimates for ``qa-pairs --dry-run``. Measured, not modelled, over the
#: 1,034 calls of D-58's sample-300 pilot: mean 2,874 input tokens of which 2,006 were the
#: cached instruction prefix (a 70% hit rate across the sweep, rising as the run warmed),
#: for a mean 510 output tokens — seven pairs plus luna's ``low`` reasoning, which is
#: billed as output. Input is nearly constant per sense, because the prompt is one gloss,
#: up to six example sentences, one capped encyclopedia passage and one etymology summary
#: whatever the entry, so unlike ``enrich``'s estimate this one is tight: it priced the
#: pilot at $0.000708 per sense against $0.000413 measured, the gap being almost entirely
#: the cache warming past the mean.
_DRY_RUN_QA_PAIRS_INPUT_TOKEN_ESTIMATE = 2900
_DRY_RUN_QA_PAIRS_CACHED_INPUT_TOKEN_ESTIMATE = 2000
_DRY_RUN_QA_PAIRS_OUTPUT_TOKEN_ESTIMATE = 510

#: How many failure messages a batch sweep keeps for the run summary.
_MAX_REPORTED_FAILURES = 5


def _mean_confidence(store: LexemeStore, lexeme_ids: Sequence[str]) -> float | None:
    """Return the mean confidence of resolved relation targets across ``lexeme_ids``.

    Args:
        store: The store to re-read the (already written) entries from.
        lexeme_ids: The entries a resolution pass changed.

    Returns:
        The mean, or ``None`` if none of the entries carry a resolved, confident
        relation target (in particular, when ``lexeme_ids`` is empty).
    """
    confidences: list[float] = []
    for lexeme_id in lexeme_ids:
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        for _, sense, _ in entry.iter_senses():
            for relation in sense.relations:
                if relation.target.sense_id is not None and relation.target.confidence is not None:
                    confidences.append(relation.target.confidence)
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


@app.command()
def generate(
    headword: Annotated[str, typer.Option("--headword", "-w", help="Word to generate.")],
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing entry.")] = False,
    no_etymology: Annotated[bool, typer.Option("--no-etymology")] = False,
    no_encyclopedia: Annotated[bool, typer.Option("--no-encyclopedia")] = False,
) -> None:
    """Generate one entry from a specification."""
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    spec = EntrySpec(
        headword=headword,
        language=cfg.language,
        with_etymology=not no_etymology,
        with_encyclopedia=not no_encyclopedia,
    )

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if not force and session.store.exists(headword):
                session.stop_reason = "already_exists"
                return session.summary(headword=headword, written=False).as_dict()
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary(headword=headword, written=False).as_dict()
            result = await generate_entry(spec, session.stages)
            async with session.store.locked(result.entry.lexeme_id):
                session.store.write(result.entry)
            await session.emit(
                session.record_for(
                    "generate",
                    result.entry.lexeme_id,
                    "ok" if result.complete else "partial",
                    cost_usd=result.cost_usd,
                )
            )
            return session.summary(
                headword=headword,
                written=True,
                senses=result.entry.sense_count(),
                edges=len(result.entry.edges()),
                failed_stages=result.failed_stages,
            ).as_dict()

    _echo_summary(_run(_main()))


@app.command()
def walk(
    seed: Annotated[list[str] | None, typer.Option("--seed", help="Seed headword(s).")] = None,
    max_new: Annotated[int, typer.Option("--max-new", help="Entry ceiling.")] = 10,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    max_seconds: Annotated[float | None, typer.Option("--max-seconds")] = None,
    strategy: Annotated[
        str, typer.Option("--strategy", help="random|least-connected|domain-deficit")
    ] = "random",
    propose_related: Annotated[bool, typer.Option("--propose-related")] = False,
    no_classifier: Annotated[bool, typer.Option("--no-classifier")] = False,
    expand_kinds: Annotated[
        str | None,
        typer.Option(
            "--expand-kinds",
            help="Comma list of kinds to expand from (default: all but function_word, affix).",
        ),
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Grow the store by walking outward from sampled entries."""
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    spec_kwargs: dict[str, Any] = {
        "seeds": list(seed or []),
        "strategy": strategy,
        "max_new_entries": max_new,
        "max_depth": max_depth,
        "max_seconds": max_seconds,
        "propose_related": propose_related,
        "use_classifier": not no_classifier,
    }
    if expand_kinds is not None:
        # WalkSpec's own default (every kind but function_word/affix) is a
        # default_factory; only override it when the flag was actually given, since
        # WalkSpec(expand_kinds=None) means "no restriction", not "use the default".
        spec_kwargs["expand_kinds"] = _parse_kinds(expand_kinds)
    spec = WalkSpec(**spec_kwargs)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary(seeds=spec.seeds).as_dict()
            outcome = await walk_graph(spec, store=session.store, runner=session.stages)
            for lexeme_id in outcome.generated:
                await session.emit(session.record_for("walk", lexeme_id, "ok"))
            session.stop_reason = outcome.stop_reason
            return session.summary(
                generated=outcome.generated,
                generated_count=outcome.generated_count,
                skipped=len(outcome.skipped),
                rejection_counts=outcome.rejection_counts,
                domain_deficit=outcome.domain_deficit,
            ).as_dict()

    _echo_summary(_run(_main()))


def _batch_targets(
    session: RunSession, from_list: Path | None, all_entries: bool, limit: int | None, offset: int
) -> list[str]:
    """Return the headwords/ids a batch enrich sweep should visit, offset and capped.

    Args:
        session: The active run session, for the store when ``all_entries`` is set.
        from_list: A word-list file, or ``None`` when ``all_entries`` selects the store.
        all_entries: Whether to visit every entry in the store.
        limit: Cap on the number of items returned, applied after ``offset``.
        offset: Number of leading items to skip.

    Returns:
        The ids to visit, in file or store order.
    """
    if from_list is not None:
        words = _read_word_list(from_list)
    else:
        words = sorted(session.store.iter_ids())
    if offset:
        words = words[offset:]
    if limit is not None:
        words = words[:limit]
    return words


def _enrich_dry_run_estimate(
    store: LexemeStore, words: Sequence[str], spec: EnrichmentSpec, cfg: AppConfig
) -> dict[str, object]:
    """Plan a batch enrich sweep without calling a model, and price the plan.

    Only rendition work is priced (``plan_renditions`` computes ``Renditions.missing``
    per owner for free); section fills are not modelled here. The cost is a rough
    estimate: real prompts vary, which is why every returned cost field says so.

    Args:
        store: The store to read entries from.
        words: The ids the sweep would visit.
        spec: The enrichment spec that would be applied.
        cfg: The run configuration, for the ``renditions`` stage's model policy.

    Returns:
        Extra summary fields describing the plan and its estimated cost.
    """
    entries_considered = 0
    entries_would_change = 0
    calls_estimated = 0
    for word in words:
        entry = store.read(word)
        if entry is None:
            continue
        entries_considered += 1
        plan = plan_renditions(entry, spec)
        if plan:
            entries_would_change += 1
            calls_estimated += len(plan)

    policy = cfg.policy(StageName.RENDITIONS)
    per_call = estimate_cost(
        policy.model,
        input_tokens=_DRY_RUN_INPUT_TOKEN_ESTIMATE,
        cached_input_tokens=_DRY_RUN_CACHED_INPUT_TOKEN_ESTIMATE,
        output_tokens=_DRY_RUN_OUTPUT_TOKEN_ESTIMATE,
        tier=policy.service_tier,
    )
    return {
        "entries_scanned": entries_considered,
        "entries_would_change": entries_would_change,
        "estimated_calls": calls_estimated,
        "estimated_cost_usd": round(per_call.total_usd * calls_estimated, 6),
        "note": "estimate only; --dry-run makes no model calls",
    }


async def _enrich_batch(
    session: RunSession, words: Sequence[str], spec: EnrichmentSpec
) -> dict[str, object]:
    """Enrich every entry in ``words`` under the run's worker pool.

    Each entry is read, enriched and (if changed) written under its own store lock, one
    ledger record per entry found. A budget stop sets ``session.stop_reason`` to
    ``"budget"`` and ends the sweep cleanly: workers finish the item in hand and stop.

    Args:
        session: The active run session (store, stages, ledger, stop event).
        words: Headwords or lexeme ids to visit, in order.
        spec: What to add to each entry.

    Returns:
        Extra summary fields: scan/change/skip/fail counts, renditions added, and up to
        five failure messages.
    """
    scanned = 0
    changed = 0
    skipped = 0
    failed = 0
    renditions_added = 0
    failures: list[str] = []

    async def handle(word: str) -> None:
        nonlocal scanned, changed, skipped, failed, renditions_added
        try:
            async with session.store.locked(word):
                entry = session.store.read(word)
                if entry is None:
                    skipped += 1
                    return
                scanned += 1
                result = await enrich_entry(entry, spec, session.stages)
                outcome = "noop"
                if result.failed_stages:
                    failed += 1
                    if len(failures) < _MAX_REPORTED_FAILURES:
                        failures.append(f"{entry.lexeme_id}: failed stages {result.failed_stages}")
                if result.changed:
                    session.store.write(result.entry)
                    changed += 1
                    renditions_added += result.renditions_added
                    outcome = "changed"
                elif not result.failed_stages:
                    skipped += 1
            await session.emit(
                session.record_for("enrich", entry.lexeme_id, outcome, cost_usd=result.cost_usd)
            )
        except BudgetExceededError:
            raise
        except OpenGlossError as exc:
            failed += 1
            if len(failures) < _MAX_REPORTED_FAILURES:
                failures.append(f"{word}: {exc}")

    await run_pool(
        words, handle, workers=session.config.concurrency.workers, stop_event=session.stop_event
    )
    if session.stop_event.is_set() and session.stop_reason == "completed":
        session.stop_reason = "budget"

    return {
        "entries_scanned": scanned,
        "entries_changed": changed,
        "entries_skipped": skipped,
        "entries_failed": failed,
        "renditions_added": renditions_added,
        "failures": failures[:5],
    }


@app.command()
def enrich(
    headword: Annotated[
        str | None, typer.Option("--headword", "-w", help="Enrich one entry.")
    ] = None,
    from_list: Annotated[
        Path | None,
        typer.Option("--from-list", help="Enrich every headword in this TSV/CSV/text file."),
    ] = None,
    all_entries: Annotated[
        bool, typer.Option("--all", help="Enrich every entry in the store.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Cap entries visited in batch mode.")
    ] = None,
    offset: Annotated[
        int, typer.Option("--offset", help="Skip this many entries in batch mode.")
    ] = 0,
    reading_levels: Annotated[str | None, typer.Option("--reading-levels")] = None,
    registers: Annotated[str | None, typer.Option("--registers")] = None,
    fields: Annotated[
        str | None,
        typer.Option("--fields", help="Comma list: gloss,examples,encyclopedia,explanation."),
    ] = None,
    sections: Annotated[
        str | None,
        typer.Option("--sections", help="Comma list: etymology,encyclopedia,explanation."),
    ] = None,
    replace: Annotated[bool, typer.Option("--replace", help="Regenerate existing fields.")] = False,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Add definition variants or missing sections to one entry, a list, or the store."""
    selectors = (headword is not None, from_list is not None, all_entries)
    if sum(selectors) != 1:
        message = "pass exactly one of --headword, --from-list, or --all"
        raise typer.BadParameter(message)
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    wanted_sections = {s.strip() for s in (sections or "").split(",") if s.strip()}
    levels = _parse_levels(reading_levels)
    styles = _parse_registers(registers)
    # Enrichment is one uniform rendition operation (docs/SCHEMA-V3.md § 6): --fields
    # picks which owner(s) get renditions, --reading-levels/--registers pick the axes,
    # crossed the same way for every selected field. No axis given means no renditions.
    renditions = (
        [
            RenditionRequest(field=field, levels=list(levels), styles=list(styles))
            for field in _parse_fields(fields)
        ]
        if levels or styles
        else []
    )
    spec = EnrichmentSpec(
        renditions=renditions,
        with_etymology="etymology" in wanted_sections,
        with_encyclopedia="encyclopedia" in wanted_sections,
        with_lexical_explanation="explanation" in wanted_sections,
        replace=replace,
    )

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if headword is not None:
                entry = session.store.read(headword)
                if entry is None:
                    session.stop_reason = "not_found"
                    return session.summary(headword=headword, changed=False).as_dict()
                if cfg.dry_run:
                    session.stop_reason = "dry_run"
                    return session.summary(headword=headword, changed=False).as_dict()
                async with session.store.locked(entry.lexeme_id):
                    result = await enrich_entry(entry, spec, session.stages)
                    if result.changed:
                        session.store.write(result.entry)
                await session.emit(
                    session.record_for(
                        "enrich",
                        entry.lexeme_id,
                        "changed" if result.changed else "noop",
                        cost_usd=result.cost_usd,
                    )
                )
                return session.summary(
                    headword=headword,
                    changed=result.changed,
                    renditions_added=result.renditions_added,
                    sections_added=result.sections_added,
                    failed_stages=result.failed_stages,
                ).as_dict()

            words = _batch_targets(session, from_list, all_entries, limit, offset)
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                extra = _enrich_dry_run_estimate(session.store, words, spec, cfg)
                return session.summary(**extra).as_dict()
            extra = await _enrich_batch(session, words, spec)
            return session.summary(**extra).as_dict()

    _echo_summary(_run(_main()))


def _examples_dry_run_estimate(
    store: LexemeStore, words: Sequence[str], cfg: AppConfig
) -> dict[str, object]:
    """Plan an ``examples`` sweep without calling a model, and price the plan.

    The plan itself is free and exact — ``workflows.examples.plan_examples`` reads each
    entry's live senses and its idempotence marker — so ``entries_due`` and
    ``sentences_planned`` are counts, not guesses. Only the money is estimated, from the
    measured per-call means above.

    Args:
        store: The store to read entries from.
        words: The ids the sweep would visit.
        cfg: The run configuration, for the example policy and the two model policies.

    Returns:
        Extra summary fields describing the plan and its estimated cost.
    """
    scanned = 0
    due = 0
    sentences = 0
    generation_calls = 0
    check_calls = 0
    for word in words:
        entry = store.read(word)
        if entry is None:
            continue
        scanned += 1
        plan = plan_examples(entry, cfg.examples)
        if not plan.due:
            continue
        due += 1
        sentences += plan.sentences
        generation_calls += 1
        check_calls += int(plan.sense_check)

    generation = cfg.policy(StageName.EXAMPLES)
    check = cfg.policy(StageName.HYGIENE)
    per_generation = estimate_cost(
        generation.model,
        input_tokens=_DRY_RUN_EXAMPLES_INPUT_TOKEN_ESTIMATE,
        cached_input_tokens=_DRY_RUN_EXAMPLES_CACHED_INPUT_TOKEN_ESTIMATE,
        output_tokens=_DRY_RUN_EXAMPLES_OUTPUT_TOKEN_ESTIMATE,
        tier=generation.service_tier,
    )
    per_check = estimate_cost(
        check.model,
        input_tokens=_DRY_RUN_EXAMPLES_CHECK_INPUT_TOKEN_ESTIMATE,
        cached_input_tokens=_DRY_RUN_EXAMPLES_CHECK_CACHED_INPUT_TOKEN_ESTIMATE,
        output_tokens=_DRY_RUN_EXAMPLES_CHECK_OUTPUT_TOKEN_ESTIMATE,
        tier=check.service_tier,
    )
    return {
        "entries_scanned": scanned,
        "entries_due": due,
        "sentences_planned": sentences,
        "estimated_calls": generation_calls + check_calls,
        "estimated_cost_usd": round(
            per_generation.total_usd * generation_calls + per_check.total_usd * check_calls, 6
        ),
        "note": "estimate only; --dry-run makes no model calls",
    }


@app.command()
def examples(
    from_list: Annotated[
        Path | None,
        typer.Option("--from-list", help="Write examples for every headword in this file."),
    ] = None,
    all_entries: Annotated[
        bool, typer.Option("--all", help="Write examples for every entry in the store.")
    ] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Cap entries visited.")] = None,
    offset: Annotated[int, typer.Option("--offset", help="Skip this many entries.")] = 0,
    per_sense: Annotated[
        int | None,
        typer.Option("--per-sense", help="Sentences per sense (default 8). Changing it re-runs."),
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Write verified, sense-disambiguated example sentences for every sense (D-53).

    One call per entry writes N fresh sentences for each of its live senses, spanning the
    configured reading levels and registers; every sentence is then checked deterministically
    (uses the headword, right length, in its readability and vocabulary band, not a definition,
    not a near-duplicate, not another sentence's opening) and, for a multi-sense entry, by one
    cheap second call asking which sense it actually illustrates. Rejected sentences are
    counted, not retried. Idempotent: an unchanged entry costs $0, and a different --per-sense
    earns exactly one more call.
    """
    if (from_list is not None) == all_entries:
        message = "pass exactly one of --from-list or --all"
        raise typer.BadParameter(message)
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    if per_sense is not None:
        cfg.examples.per_sense = per_sense

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            words = _batch_targets(session, from_list, all_entries, limit, offset)
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                extra = _examples_dry_run_estimate(session.store, words, cfg)
                return session.summary(**extra).as_dict()
            outcome = await run_examples(
                session.store,
                session.stages,
                lexeme_ids=words,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command()
def resolve(
    headword: Annotated[
        str | None, typer.Option("--headword", "-w", help="Resolve one entry.")
    ] = None,
    resolve_all: Annotated[
        bool, typer.Option("--all", help="Resolve every entry in the store.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Cap entries visited by --all.")
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Resolve relation targets to sense ids, for one entry or the whole store."""
    if bool(headword) == resolve_all:
        message = "pass exactly one of --headword or --all"
        raise typer.BadParameter(message)
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary(headword=headword, all=resolve_all).as_dict()
            if resolve_all:
                outcome = await resolve_store(
                    session.store, session.stages, limit=limit, stop_event=session.stop_event
                )
                if outcome.stopped_reason:
                    session.stop_reason = outcome.stopped_reason
            elif headword is None:
                # Unreachable: the XOR check above guarantees a headword here. Typed
                # explicitly so the type checker sees `headword` narrowed to `str` below.
                raise typer.BadParameter("--headword is required without --all")
            else:
                entry = session.store.read(headword)
                if entry is None:
                    session.stop_reason = "not_found"
                    return session.summary(headword=headword, resolved=0).as_dict()
                async with session.store.locked(entry.lexeme_id):
                    outcome = await resolve_entry(entry, session.store, session.stages)
                    if outcome.changed:
                        session.store.write(entry)
            for lexeme_id in outcome.entries_changed:
                await session.emit(session.record_for("resolve", lexeme_id, "ok"))
            return session.summary(
                headword=headword,
                resolved=outcome.resolved,
                declined=outcome.declined,
                absent_targets=outcome.absent_targets,
                unresolved=outcome.declined + outcome.absent_targets,
                mean_confidence=_mean_confidence(session.store, outcome.entries_changed),
            ).as_dict()

    _echo_summary(_run(_main()))


@app.command()
def retrofit(
    only: Annotated[str, typer.Option("--only", help="classify_kind|tag_domain|spans|all")] = "all",
    limit: Annotated[
        int | None, typer.Option("--limit", help="Cap entries visited per pass.")
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Backfill kind, domain, and example spans over an existing store, idempotently."""
    if only not in (*RetrofitPass.ALL, "all"):
        message = f"--only must be one of {(*RetrofitPass.ALL, 'all')}"
        raise typer.BadParameter(message)
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    passes = None if only == "all" else [only]

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary(only=only).as_dict()
            outcome = await run_retrofit(
                session.store,
                session.stages,
                only=passes,
                limit=limit,
                stop_event=session.stop_event,
            )
            if outcome.stopped_reason:
                session.stop_reason = outcome.stopped_reason
            for name, result in outcome.passes.items():
                await session.emit(
                    session.record_for("retrofit", name, "ok", cost_usd=result.cost_usd)
                )
            return session.summary(
                passes={
                    name: {
                        "entries_scanned": result.entries_scanned,
                        "entries_changed": result.entries_changed,
                        "items_changed": result.items_changed,
                        "calls": result.calls,
                        "cost_usd": round(result.cost_usd, 6),
                        "metrics": result.metrics,
                    }
                    for name, result in outcome.passes.items()
                },
            ).as_dict()

    _echo_summary(_run(_main()))


def _qa_estimate(cfg: AppConfig, entries: int) -> dict[str, object]:
    """Price a QA sweep of ``entries`` entries without calling a model.

    Args:
        cfg: The run configuration, for the ``qa`` stage's model policy.
        entries: How many entries the sweep would judge.

    Returns:
        Summary fields describing the projected cost. The judge is billed per entry, one
        call each, so this is a far tighter estimate than ``enrich --dry-run``'s.
    """
    policy = cfg.policy(StageName.QA)
    per_call = estimate_cost(
        policy.model,
        input_tokens=_DRY_RUN_QA_INPUT_TOKEN_ESTIMATE,
        output_tokens=_DRY_RUN_QA_OUTPUT_TOKEN_ESTIMATE,
        tier=policy.service_tier,
    )
    return {
        "judge_model": policy.model,
        "estimated_calls": entries,
        "estimated_cost_usd_per_entry": round(per_call.total_usd, 6),
        "estimated_cost_usd": round(per_call.total_usd * entries, 6),
        "note": "estimate only; --dry-run makes no model calls",
    }


@app.command()
def qa(
    from_list: Annotated[
        Path, typer.Option("--from-list", help="Headword list to sample from, in rank order.")
    ],
    sample: Annotated[int, typer.Option("--sample", help="How many entries to judge.")] = 50,
    seed: Annotated[int, typer.Option("--seed", help="Seed for the stratified draw.")] = 0,
    force: Annotated[
        bool, typer.Option("--force", help="Re-judge entries that already carry a verdict.")
    ] = False,
    report: Annotated[
        Path | None, typer.Option("--report", help="Write the metrics JSON to this path.")
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Judge a stratified sample of entries with the QA model and record the verdicts."""
    if sample < 1:
        message = "--sample must be at least 1"
        raise typer.BadParameter(message)
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    words = _read_word_list(from_list)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            ids = stratified_sample(session.store, words, sample, seed)
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary(
                    sample=ids, sample_size=len(ids), seed=seed, qa=_qa_estimate(cfg, len(ids))
                ).as_dict()
            outcome: QAOutcome = await run_qa(
                session.store,
                session.stages,
                lexeme_ids=ids,
                stop_event=session.stop_event,
                force=force,
            )
            if outcome.stopped_reason:
                session.stop_reason = outcome.stopped_reason
            for lexeme_id in outcome.entries_changed:
                await session.emit(session.record_for("qa", lexeme_id, "judged"))
            metrics = outcome.as_dict()
            if report is not None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
            return session.summary(
                sample_size=len(ids), seed=seed, report=str(report) if report else None, qa=metrics
            ).as_dict()

    _echo_summary(_run(_main()))


def _qa_pairs_dry_run_estimate(
    store: LexemeStore, words: Sequence[str], cfg: AppConfig
) -> dict[str, object]:
    """Plan a ``qa-pairs`` sweep without calling a model, and price the plan.

    The plan itself is free and exact — ``workflows.qa_pairs.plan_qa_pairs`` reads each
    sense's sources and its D-47 marker — so ``senses_due`` and ``pairs_planned`` are
    counts, not guesses. Only the money is estimated, from the measured per-call means
    above.

    Args:
        store: The store to read entries from.
        words: The ids the sweep would visit.
        cfg: The run configuration, for the ``qa_pairs`` model policy.

    Returns:
        Extra summary fields describing the plan and its estimated cost.
    """
    scanned = 0
    entries_due = 0
    senses = 0
    pairs = 0
    for word in words:
        entry = store.read(word)
        if entry is None:
            continue
        scanned += 1
        plan = plan_qa_pairs(entry)
        if not plan.due:
            continue
        entries_due += 1
        senses += plan.senses
        pairs += plan.pairs

    policy = cfg.policy(StageName.QA_PAIRS)
    per_call = estimate_cost(
        policy.model,
        input_tokens=_DRY_RUN_QA_PAIRS_INPUT_TOKEN_ESTIMATE,
        cached_input_tokens=_DRY_RUN_QA_PAIRS_CACHED_INPUT_TOKEN_ESTIMATE,
        output_tokens=_DRY_RUN_QA_PAIRS_OUTPUT_TOKEN_ESTIMATE,
        tier=policy.service_tier,
    )
    return {
        "entries_scanned": scanned,
        "entries_due": entries_due,
        "senses_due": senses,
        "pairs_planned": pairs,
        "estimated_calls": senses,
        "estimated_cost_usd_per_sense": round(per_call.total_usd, 6),
        "estimated_cost_usd": round(per_call.total_usd * senses, 6),
        "note": "estimate only; --dry-run makes no model calls",
    }


@app.command("qa-pairs")
def qa_pairs(
    from_list: Annotated[
        Path | None,
        typer.Option("--from-list", help="Write pairs for every headword in this file."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Cap entries visited.")] = None,
    offset: Annotated[int, typer.Option("--offset", help="Skip this many entries.")] = 0,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Write grounded question/answer pairs for every live sense (D-58).

    One call per sense buys seven pairs, one of each question type, at mixed difficulty,
    answered only from that sense's own stored text — its canonical gloss, its example
    sentences, its entry's encyclopedia passage and etymology — each labelled with an id
    the answer must cite. A pair citing an id that was not supplied, or whose answer
    shares fewer than two content words with what it cited, or which repeats a question
    already asked, is dropped and counted. Idempotent: an unchanged sense costs $0, and a
    sense whose gloss or sources changed earns one more call, up to two in all.

    Without --from-list the whole store is visited. This command is not `opengloss qa`,
    which is the Opus judge scoring stored content.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            words = _batch_targets(session, from_list, from_list is None, limit, offset)
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                extra = _qa_pairs_dry_run_estimate(session.store, words, cfg)
                return session.summary(**extra).as_dict()

            async def emit(call: QACallRecord) -> None:
                await session.emit(
                    session.record_for(
                        "qa_pairs",
                        call.sense_id,
                        "written" if call.accepted else "empty",
                        cost_usd=call.cost_usd,
                        input_tokens=call.input_tokens,
                        cached_input_tokens=call.cached_input_tokens,
                        output_tokens=call.output_tokens,
                        attempts=call.attempts,
                        duration_seconds=round(call.duration_seconds, 3),
                        detail={"generated": call.generated, "accepted": call.accepted},
                    )
                )

            outcome = await run_qa_pairs(
                session.store,
                session.stages,
                lexeme_ids=words,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
                on_call=emit,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


_MIGRATE_VERSIONS = ("auto", "1.3", "2.0")


@app.command(name="migrate")
def migrate_cmd(
    from_path: Annotated[
        Path, typer.Option("--from", help="A single legacy JSON file, or a directory of them.")
    ],
    version: Annotated[str, typer.Option("--version", help="auto|1.3|2.0")] = "auto",
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an entry already in the store.")
    ] = False,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
) -> None:
    """Upgrade v1.3/v2.0 payloads into the v3 store. Makes no model calls."""
    if version not in _MIGRATE_VERSIONS:
        raise typer.BadParameter(f"--version must be one of {_MIGRATE_VERSIONS}")
    if not from_path.exists():
        raise typer.BadParameter(f"--from path does not exist: {from_path}")
    cfg = _build_config(config_path, store, None, None)
    paths = sorted(from_path.rglob("*.json")) if from_path.is_dir() else [from_path]

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            migrated = 0
            skipped = 0
            failed = 0
            failures: list[str] = []
            by_version: dict[str, int] = {}
            for path in paths:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    detected = detect_version(payload)
                    by_version[detected] = by_version.get(detected, 0) + 1
                    if version == "auto":
                        entry = migrate_payload(payload)
                    elif version == "1.3":
                        entry = migrate_from_v13(payload)
                    else:
                        entry = migrate_from_v2(payload)
                except Exception as exc:  # one bad file must not abort the whole sweep
                    failed += 1
                    failures.append(f"{path}: {exc}")
                    continue
                async with session.store.locked(entry.lexeme_id):
                    if session.store.exists(entry.lexeme_id) and not force:
                        skipped += 1
                        continue
                    session.store.write(entry)
                await session.emit(session.record_for("migrate", entry.lexeme_id, "ok"))
                migrated += 1
            return session.summary(
                files=len(paths),
                migrated=migrated,
                skipped=skipped,
                failed=failed,
                by_version=by_version,
                failures=failures[:5],
            ).as_dict()

    _echo_summary(_run(_main()))


@app.command("graph-hygiene")
def graph_hygiene(
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Repair the resolved semantic graph deterministically: no model calls, no deletions.

    Self-loops and cycle-breaking back-edges are demoted to ``see_also``, mutual
    hypernyms to ``synonym``, and symmetric relations are completed with a provenance
    note (D-43). ``--dry-run`` computes the plan and writes nothing.
    """
    cfg = _build_config(config_path, store, None, concurrency, dry_run)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            outcome = await run_graph_hygiene(
                session.store,
                None,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
                dry_run=cfg.dry_run,
            )
            if outcome.stopped_reason:
                session.stop_reason = outcome.stopped_reason
            elif cfg.dry_run:
                session.stop_reason = "dry_run"
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command("example-hygiene")
def example_hygiene(
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Regenerate example renditions that do not use the headword (D-45).

    One call per affected entry; a replacement is adopted only if the headword (or an
    inflected form) is found in it. Idempotent; entries with nothing to fix cost $0.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary().as_dict()
            outcome = await run_example_hygiene(
                session.store,
                session.stages,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**asdict(outcome)).as_dict()

    _echo_summary(_run(_main()))


@app.command("content-hygiene")
def content_hygiene(
    only: Annotated[
        str | None,
        typer.Option("--only", help="Comma list of steps (self_synonym, synonym_antonym, ...)."),
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Repair content defects found by QA (D-49).

    Relation contradictions, garbage or stilted examples, and degenerate renditions.
    Free steps run first; every step is idempotent.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    steps = {s.strip() for s in only.split(",") if s.strip()} if only else None

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary().as_dict()
            outcome = await run_content_hygiene(
                session.store,
                session.stages,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
                only=steps,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command("relation-hygiene")
def relation_hygiene(
    only: Annotated[
        str | None,
        typer.Option("--only", help="Comma list of steps (inflections, meta_labels, ...)."),
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Demote or retype relations the QA judge found untrue (D-50).

    Inflected duplicates, modifier phrases built on the headword and meta-labels are
    settled by rule for $0; what is left gets one nano verdict per relation. Free steps
    run first; every step is idempotent; nothing is ever deleted. Run after
    ``graph-hygiene``, whose reciprocity step can otherwise re-add a demoted synonym from
    a far side that was never demoted with it.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    steps = {s.strip() for s in only.split(",") if s.strip()} if only else None

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary().as_dict()
            outcome = await run_relation_hygiene(
                session.store,
                session.stages,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
                only=steps,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command("sense-hygiene")
def sense_hygiene(
    only: Annotated[
        str | None,
        typer.Option("--only", help="Comma list of steps (distinctness, example_fit)."),
    ] = None,
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Merge near-duplicate senses and refile misplaced examples (D-52).

    One nano call per multi-sense entry per step: the first retires a sense that repeats a
    lower-indexed one, merging its examples, relations and renditions onto the survivor; the
    second moves a canonical example to the sense it actually illustrates. Nothing is deleted
    and no sense is renumbered; every step is idempotent, and a single-sense entry costs $0.
    Run ``retrofit --only repair`` afterwards to refill any sense left without an example.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)
    steps = {s.strip() for s in only.split(",") if s.strip()} if only else None

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary().as_dict()
            outcome = await run_sense_hygiene(
                session.store,
                session.stages,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
                only=steps,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command("vocabulary-hygiene")
def vocabulary_hygiene(
    config_path: _ConfigOpt = None,
    store: _StoreOpt = None,
    budget: _BudgetOpt = None,
    concurrency: _ConcurrencyOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Rewrite grade_1/grade_5 renditions whose vocabulary is too hard (D-51).

    Uses the Dale-Chall familiar-word share beside Flesch-Kincaid; accepts a rewrite
    only if it lowers the share, stays in its reading band, and keeps the headword.
    """
    cfg = _build_config(config_path, store, budget, concurrency, dry_run)

    async def _main() -> dict[str, object]:
        async with RunSession(cfg, install_signal_handler=True) as session:
            if cfg.dry_run:
                session.stop_reason = "dry_run"
                return session.summary().as_dict()
            outcome = await run_vocabulary_hygiene(
                session.store,
                session.stages,
                workers=cfg.concurrency.workers,
                stop_event=session.stop_event,
            )
            if outcome.stopped_reason is not None:
                session.stop_reason = outcome.stopped_reason
            return session.summary(**outcome.as_dict()).as_dict()

    _echo_summary(_run(_main()))


@app.command()
def show(
    headword: Annotated[str, typer.Option("--headword", "-w")],
    store: _StoreOpt = None,
    config_path: _ConfigOpt = None,
    edges: Annotated[bool, typer.Option("--edges", help="Print derived edges instead.")] = False,
) -> None:
    """Print a stored entry, or its derived edge list."""
    cfg = _build_config(config_path, store, None, None)
    entry = LexemeStore(cfg.store).read(headword)
    if entry is None:
        typer.echo(f"not found: {headword}", err=True)
        raise typer.Exit(code=1)
    if edges:
        typer.echo(json.dumps([e.model_dump(mode="json") for e in entry.edges()], indent=2))
    else:
        typer.echo(json.dumps(entry.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command()
def stats(store: _StoreOpt = None, config_path: _ConfigOpt = None) -> None:
    """Summarise the contents of a store."""
    cfg = _build_config(config_path, store, None, None)
    lexeme_store = LexemeStore(cfg.store)
    entries = senses = renditions = edges = 0
    by_level: dict[str, int] = {}
    for entry in lexeme_store.iter_entries():
        entries += 1
        senses += entry.sense_count()
        edges += len(entry.edges())
        for _, sense, _ in entry.iter_senses():
            # v3 compile fix: gloss renditions replace the old variant list. The canonical
            # (neutral, plain) rendition is the definition itself, not an added variant,
            # so it is not counted here.
            for rendition in sense.gloss:
                if rendition.is_canonical:
                    continue
                renditions += 1
                key = f"{rendition.reading_level.value}/{rendition.style.value}"
                by_level[key] = by_level.get(key, 0) + 1
    _echo_summary(
        {
            "store": str(lexeme_store.root),
            "entries": entries,
            "senses": senses,
            "renditions": renditions,
            "edges": edges,
            "senses_per_entry": round(senses / entries, 3) if entries else 0.0,
            "renditions_by_target": by_level,
        }
    )


@app.command()
def audit(
    from_list: Annotated[
        Path | None,
        typer.Option("--from-list", help="Restrict the audit to these headwords."),
    ] = None,
    store: _StoreOpt = None,
    config_path: _ConfigOpt = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--no-json", help="Print full JSON (default) or the top gaps."),
    ] = True,
) -> None:
    """Measure a store against the pristine-entry definition (``docs/CORE-DIARY.md``)."""
    cfg = _build_config(config_path, store, None, None)
    lexeme_store = LexemeStore(cfg.store)
    core_words = set(_read_word_list(from_list)) if from_list is not None else None
    report: AuditReport = audit_store(lexeme_store, core_words)
    if json_output:
        _echo_summary(report.as_dict())
    else:
        typer.echo(f"entries_total: {report.entries_total}")
        for gap in report.top_gaps(5):
            typer.echo(f"- {gap}")


@app.command()
def price(
    stage: Annotated[str | None, typer.Option("--stage", help="Show one stage's policy.")] = None,
    config_path: _ConfigOpt = None,
) -> None:
    """Show the price table and the per-stage model policy."""
    cfg = _build_config(config_path, None, None, None)
    stages = [StageName(stage)] if stage else list(StageName)
    policies = {
        s.value: {
            "model": cfg.policy(s).model,
            "service_tier": cfg.policy(s).service_tier.value,
            "reasoning_effort": cfg.policy(s).reasoning_effort,
            "input_usd_per_mtok": PRICE_TABLE[
                (cfg.policy(s).model, cfg.policy(s).service_tier)
            ].input_usd,
            "output_usd_per_mtok": PRICE_TABLE[
                (cfg.policy(s).model, cfg.policy(s).service_tier)
            ].output_usd,
        }
        for s in stages
    }
    _echo_summary(
        {
            "pricing_as_of": PRICING_AS_OF.isoformat(),
            "sources": list(PRICING_SOURCES),
            "default_tier": ServiceTier.FLEX.value,
            "policies": policies,
        }
    )


def _run(coro: Coroutine[Any, Any, dict[str, object]]) -> dict[str, object]:
    """Run a coroutine, converting our exceptions into a clean CLI failure."""
    try:
        return asyncio.run(coro)
    except OpenGlossError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":  # pragma: no cover
    app()
