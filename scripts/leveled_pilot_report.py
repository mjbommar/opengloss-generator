r"""L1 pilot report for docs/LEVELED-PRETRAIN-PLAN.md § 6.

Reads a pilot store after the three leveled passes and measures, per
``(field, level, register)`` cell, the generation-time flags the stage stored
(readability miss, headword-initial, near-copy, headword-absent, hard vocabulary); for
contrast renditions, whether both terms of the pair are named; and the near-copy rate
between the ``grade_5`` and ``college`` rendition of the same target. With ``--judge N``
it also sends N items, balanced across cells, to a blinded ``claude-opus-5`` judge for
meaning preservation, level appropriateness and (contrasts) verdict consistency.

Usage:
    uv run python scripts/leveled_pilot_report.py --store /data1/.../pilot-store \\
        --list /data1/.../pilot.txt --judge 100 --out reports/leveled-pilot/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from opengloss_generator import spans
from opengloss_generator.config import StoreConfig
from opengloss_generator.hygiene import is_near_copy
from opengloss_generator.schema import QAFlag, ReadingLevel, Register
from opengloss_generator.store import LexemeStore

LEVELS = (ReadingLevel.GRADE_5, ReadingLevel.COLLEGE)
GLOSS_REGISTERS = (Register.INFORMAL, Register.FORMAL, Register.TECHNICAL)
JUDGE = "claude-opus-5"
JUDGE_RATES = (5.00, 25.00)  # USD per 1M input / output tokens
SEED = 20260924
FLAGS = (
    QAFlag.OG_READABILITY_MISS,
    QAFlag.OG_HEADWORD_INITIAL,
    QAFlag.OG_NEAR_COPY,
    QAFlag.OG_HEADWORD_ABSENT,
    QAFlag.OG_HARD_VOCABULARY,
)
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> str:
    """Return ``text`` as space-joined lowercase words, padded for whole-word search."""
    return " " + " ".join(_WORD.findall(text.casefold())) + " "


def _names(text: str, term: str) -> bool:
    """Whether ``term`` appears as whole words in ``text``."""
    wanted = " ".join(_WORD.findall(term.casefold()))
    return bool(wanted) and f" {wanted} " in _words(text)


def collect(store: LexemeStore, ids: list[str]) -> list[dict[str, Any]]:
    """Return one item per leveled rendition the pilot passes produced."""
    items: list[dict[str, Any]] = []
    for lexeme_id in ids:
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        forms = list(spans.generate_forms(entry.headword))
        for _pos, sense, sid in entry.iter_senses():
            if sense.retired:
                continue
            for rendition in sense.gloss:
                if rendition.reading_level in LEVELS and rendition.style in GLOSS_REGISTERS:
                    items.append(
                        {
                            "field": "gloss",
                            "owner": sid,
                            "headword": entry.headword,
                            "level": rendition.reading_level.value,
                            "register": rendition.style.value,
                            "text": rendition.content,
                            "source": sense.canonical_gloss(),
                            "flags": [f.value for f in rendition.assessment.qa_flags]
                            if rendition.assessment
                            else [],
                        }
                    )
        for rendition in entry.lexical_explanation:
            if rendition.reading_level in LEVELS:
                canonical = entry.lexical_explanation.canonical()
                items.append(
                    {
                        "field": "explanation",
                        "owner": entry.lexeme_id,
                        "headword": entry.headword,
                        "level": rendition.reading_level.value,
                        "register": rendition.style.value,
                        "text": rendition.content,
                        "source": canonical.content if canonical else "",
                        "flags": [f.value for f in rendition.assessment.qa_flags]
                        if rendition.assessment
                        else [],
                    }
                )
        for contrast in entry.contrasts:
            target = contrast.edge_id.rsplit("->", 1)[-1].replace("_", " ")
            for rendition in contrast.text:
                if rendition.reading_level in LEVELS:
                    text = rendition.content
                    items.append(
                        {
                            "field": "contrast",
                            "owner": contrast.edge_id,
                            "headword": entry.headword,
                            "target": target,
                            "verdict": contrast.verdict.value,
                            "level": rendition.reading_level.value,
                            "register": rendition.style.value,
                            "text": text,
                            "source": contrast.canonical_text(),
                            "names_headword": spans.find_span(text, entry.headword, forms)
                            is not None,
                            "names_target": _names(text, target),
                            "flags": [f.value for f in rendition.assessment.qa_flags]
                            if rendition.assessment
                            else [],
                        }
                    )
    return items


def measure(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate flags, contrast naming and between-level near copies per cell."""
    cells: dict[str, Counter[str]] = defaultdict(Counter)
    for item in items:
        cell = f"{item['field']}/{item['level']}/{item['register']}"
        cells[cell]["n"] += 1
        for flag in item["flags"]:
            cells[cell][flag] += 1
        cells[cell]["near_copy_of_source"] += is_near_copy(item["text"], item["source"])
        if item["field"] == "contrast":
            cells[cell]["names_headword"] += item["names_headword"]
            cells[cell]["names_target"] += item["names_target"]
            cells[cell]["names_both"] += item["names_headword"] and item["names_target"]
    by_target: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for item in items:
        by_target[(item["field"], item["owner"], item["register"])][item["level"]] = item["text"]
    pairs = [t for t in by_target.values() if len(t) == len(LEVELS)]
    between = sum(is_near_copy(t["grade_5"], t["college"]) for t in pairs)
    report_cells = {}
    for cell, counts in sorted(cells.items()):
        n = counts["n"]
        report_cells[cell] = {"n": n} | {
            k: round(v / n, 4) for k, v in sorted(counts.items()) if k != "n"
        }
    return {
        "renditions": len(items),
        "cells": report_cells,
        "level_pairs": len(pairs),
        "level_pair_near_copy_share": round(between / len(pairs), 4) if pairs else None,
    }


JUDGE_PROMPT = """You are auditing rewritten reference text. Judge strictly; you do not know \
who wrote it.

FIELD: {field}
HEADWORD: {headword}{extra}
TARGET AUDIENCE: reading level = {level}; register = {register}
  (grade_5 = a ten-year-old: short sentences, everyday words; college = an adult reader, \
precise; informal / formal / technical / plain = the voice.)

SOURCE (the meaning that must be preserved):
{source}

REWRITE:
{text}

Answer with ONLY a JSON object:
{{"meaning_preserved": true|false, "level_appropriate": true|false, \
"register_appropriate": true|false, "verdict_consistent": true|false|null, \
"note": "<=20 words"}}
verdict_consistent is null unless FIELD is contrast; then it says whether the rewrite keeps \
the source's judgement about how the two terms relate."""


def _parse_verdict(raw: str) -> dict[str, Any] | None:
    """Parse the judge's JSON object, tolerating fences and prose around it."""
    for match in re.finditer(r"\{[^{}]*\}", raw, re.S):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "meaning_preserved" in value:
            return value
    return None


async def judge(items: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """Send ``n`` cell-balanced items to the judge and aggregate its verdicts."""
    rng = random.Random(SEED)  # noqa: S311 - deterministic sampling
    by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_cell[f"{item['field']}/{item['level']}/{item['register']}"].append(item)
    chosen: list[dict[str, Any]] = []
    cells = sorted(by_cell)
    while len(chosen) < n and any(by_cell.values()):
        for cell in cells:
            if by_cell[cell] and len(chosen) < n:
                chosen.append(by_cell[cell].pop(rng.randrange(len(by_cell[cell]))))
    sem = asyncio.Semaphore(12)
    results: list[dict[str, Any]] = []
    cost = 0.0

    async with httpx.AsyncClient() as client:

        async def one(item: dict[str, Any]) -> None:
            nonlocal cost
            extra = ""
            if item["field"] == "contrast":
                extra = f"\nRELATED TERM: {item['target']}\nSTORED VERDICT: {item['verdict']}"
            prompt = JUDGE_PROMPT.format(extra=extra, **item)
            async with sem:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json={
                        "model": JUDGE,
                        "max_tokens": 1200,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    headers={
                        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                        "anthropic-version": "2023-06-01",
                    },
                    timeout=300,
                )
            data = response.json()
            usage = data.get("usage", {})
            cost += (
                usage.get("input_tokens", 0) * JUDGE_RATES[0]
                + usage.get("output_tokens", 0) * JUDGE_RATES[1]
            ) / 1e6
            raw = "".join(b.get("text", "") for b in data.get("content", []))
            verdict = _parse_verdict(raw)
            results.append({**item, "judge": verdict, "judge_raw": raw})

        await asyncio.gather(*(one(item) for item in chosen))

    agg: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        verdict = result["judge"]
        cell = f"{result['field']}/{result['level']}/{result['register']}"
        for key in (cell, "all", result["field"]):
            if not verdict:
                agg[key]["unparsed"] += 1
                continue
            agg[key]["n"] += 1
            for k in ("meaning_preserved", "level_appropriate", "register_appropriate"):
                agg[key][k] += bool(verdict.get(k))
            if verdict.get("verdict_consistent") is not None:
                agg[key]["verdict_scored"] += 1
                agg[key]["verdict_consistent"] += bool(verdict["verdict_consistent"])
    summary = {}
    for key, counts in sorted(agg.items()):
        n = counts["n"]
        row: dict[str, Any] = {"n": n}
        for k in ("meaning_preserved", "level_appropriate", "register_appropriate"):
            row[k] = round(counts[k] / n, 4) if n else None
        if counts["verdict_scored"]:
            row["verdict_consistent"] = round(
                counts["verdict_consistent"] / counts["verdict_scored"], 4
            )
        row["unparsed"] = counts["unparsed"]
        summary[key] = row
    return {
        "judge": JUDGE,
        "items": len(results),
        "cost_usd": round(cost, 4),
        "by": summary,
        "results": results,
    }


def main() -> None:
    """Build the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--list", type=Path, required=True)
    parser.add_argument("--judge", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    store = LexemeStore(StoreConfig(root=args.store))
    ids = args.list.read_text(encoding="utf-8").split()
    items = collect(store, ids)
    report: dict[str, Any] = {"pilot_entries": len(ids), **measure(items)}
    if args.judge:
        report["judge"] = asyncio.run(judge(items, args.judge))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
