"""Run one arm of the writer-diversity pilot (D-63): one writer, one of the two tasks.

Frozen spec: the same 300 entries built by ``build_sample_writers.py``, the same two
requests (graded gloss renditions; per-sense examples, D-53), only the writer's model
varies. Each arm writes into its own copy of the sample store
(``data/sample-writers-<arm>/``) so arms never clobber each other, and its own ledger
under ``runs/``, exactly the pipeline's normal accounting — nothing here is a special
code path, only a different ``ModelPolicy.model`` for the two prose stages.

Usage:
    uv run python scripts/run_writer_pilot.py --arm luna --task renditions --budget 0.75
    uv run python scripts/run_writer_pilot.py --arm luna --task examples --budget 0.75
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from opengloss_generator.cli import _enrich_batch
from opengloss_generator.config import load_config
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import ReadingLevel, StageName
from opengloss_generator.workflows.enrich import EnrichmentSpec, RenditionField, RenditionRequest
from opengloss_generator.workflows.examples import run_examples

#: The pilot's five arms (D-63), plus D-64's Round 2 arms. Bare model ids: OpenAI and
#: Anthropic route by their own naming convention, OpenRouter ids by their catalogue's
#: ``org/model`` shape, Gemini by its ``gemini-`` prefix (see ``router._split_model``).
#: Round 2's two free OpenRouter models carry an explicit ``openrouter:`` prefix even
#: though their ``org/model:free`` shape would already route there on its own — stated
#: for clarity, since the ``:free`` suffix inside the id could otherwise be misread as
#: this project's ``prefix:model`` routing syntax (``_split_model`` only treats the
#: *first* colon that way, so ``openrouter:z-ai/glm-5.2:free`` correctly resolves to
#: kind ``openrouter``, bare id ``z-ai/glm-5.2:free``).
WRITERS: dict[str, str] = {
    "luna": "gpt-5.6-luna",
    "qwen": "qwen/qwen3.5-397b-a17b",
    "haiku": "claude-haiku-4-5",
    "gemini": "gemini-3.7-flash",
    "deepseek": "deepseek/deepseek-v4-pro",
    # D-64 Round 2 arms.
    "google": "gemini-3.8-flash",
    "glm": "openrouter:z-ai/glm-5.2:free",
    "nemotron": "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
}

_GRADED_LEVELS = [
    ReadingLevel.GRADE_1,
    ReadingLevel.GRADE_5,
    ReadingLevel.GRADE_10,
    ReadingLevel.COLLEGE,
]

_HEADWORD_LIST = Path("data/sample-writers.tsv")


def _headwords() -> list[str]:
    return [line for line in _HEADWORD_LIST.read_text(encoding="utf-8").splitlines() if line]


async def _run_renditions(session: RunSession, words: list[str]) -> dict[str, object]:
    """Task (a): graded gloss renditions at grade_1/5/10/college, on the ``examples`` field.

    ``RenditionField.EXAMPLES`` here names the *field being rewritten* (the sense's
    stored example text), not the D-53 ``examples`` workflow below — the pilot spec
    calls these "graded example renditions" for exactly that field.
    """
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=_GRADED_LEVELS)]
    )
    return await _enrich_batch(session, words, spec)


async def _run_examples(session: RunSession, words: list[str]) -> dict[str, object]:
    """Task (b): per-sense example sentences (D-53), 8 per sense."""
    outcome = await run_examples(
        session.store,
        session.stages,
        lexeme_ids=words,
        workers=session.config.concurrency.workers,
        stop_event=session.stop_event,
    )
    if outcome.stopped_reason is not None:
        session.stop_reason = outcome.stopped_reason
    return outcome.as_dict()


async def _main(
    arm: str,
    task: str,
    budget: float,
    concurrency: int,
    limit: int | None = None,
    requests_per_minute: int | None = None,
    max_tokens: int | None = None,
) -> None:
    writer = WRITERS[arm]
    cfg = load_config(budget_usd=budget)
    cfg.store.root = Path(f"data/sample-writers-{arm}")
    cfg.concurrency.workers = concurrency
    if requests_per_minute is not None:
        # D-64 Round 2: OpenRouter's free tier (":free" models) rate-limits well below
        # this project's default 480/min, independently of any paid quota. Set from the
        # CLI rather than baked into `ConcurrencyConfig`, since it is a property of the
        # arm's provider tier, not of this pipeline.
        cfg.concurrency.requests_per_minute = requests_per_minute
    if max_tokens is not None:
        # D-64 Round 2: both free arms are reasoning models that do not reliably honour
        # `reasoning_effort="low"` (qwen's D-63 blowup was the same failure mode) —
        # capping `max_tokens` bounds a single call's worst-case latency and cost
        # instead of letting a hidden-reasoning blowup run for minutes. Also lowers
        # `expected_output_tokens` proportionally so the budget reservation this policy
        # makes per call does not itself starve dispatch (D-41's own reasoning, applied
        # here rather than left at the much larger prose-stage default).
        for stage in (StageName.RENDITIONS, StageName.EXAMPLES):
            policy = cfg.policies[stage]
            policy.max_tokens = max_tokens
            policy.expected_output_tokens = min(policy.expected_output_tokens, max_tokens // 2)
    cfg.policies[StageName.RENDITIONS].model = writer
    cfg.policies[StageName.EXAMPLES].model = writer
    words = _headwords()
    if limit is not None:
        words = words[:limit]

    async with RunSession(cfg) as session:
        if task == "renditions":
            extra = await _run_renditions(session, words)
        else:
            extra = await _run_examples(session, words)
        summary = session.summary(arm=arm, writer=writer, task=task, **extra)

    print(f"=== {arm} ({writer}) / {task} ===")
    print(f"run_id: {summary.run_id}")
    print(f"stop_reason: {summary.stop_reason}")
    print(f"cost: ${summary.cost.total_usd:.6f}  calls: {summary.cost.calls}")
    for key, value in extra.items():
        if key != "failures":
            print(f"{key}: {value}")
    if extra.get("failures"):
        print("failures (up to 5):", extra["failures"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=sorted(WRITERS))
    parser.add_argument("--task", required=True, choices=("renditions", "examples"))
    parser.add_argument("--budget", type=float, default=0.75)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test on the first N words.")
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=None,
        help="Override ConcurrencyConfig.requests_per_minute (D-64: free-tier rate caps).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override RENDITIONS/EXAMPLES max_tokens (D-64: bound a reasoning blowup).",
    )
    args = parser.parse_args()
    asyncio.run(
        _main(
            args.arm,
            args.task,
            args.budget,
            args.concurrency,
            args.limit,
            args.requests_per_minute,
            args.max_tokens,
        )
    )
