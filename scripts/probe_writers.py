"""Small blinded writer probe for the encoder-data expansion round (not a G1 acceptance).

Twelve fixed senses (tiers core-6, spread across domains) x two tasks x each candidate
writer; every writer receives identical inputs. Task ``doc``: a headword-free,
source-grounded passage for a fixed communicative purpose. Task ``queries``: four
realistic information-need queries of new styles. Deterministic checks (schema,
headword leakage, length band, tokens, cost, latency, service tier actually granted)
plus one blinded listwise judgement per item by a model family that wrote none of them.

Outputs land in ``--out`` (JSON with every raw output). Costs are computed from the
provider-reported usage and the rates in ``RATES`` (verified 2026-09-23).

Usage:
    uv run python scripts/probe_writers.py --out reports/writer-probe-2026-09-23/probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pyarrow.compute as pc
import pyarrow.dataset as ds

HF = Path("data/hf")
SEED = 20260923

#: USD per 1M tokens: (input, cached input, output), standard tier, verified 2026-09-23
#: against developers.openai.com/api/docs/pricing and ai.google.dev/gemini-api/docs/pricing.
#: Batch/flex are half of these for every row.
RATES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "claude-opus-5": (5.00, 0.50, 25.00),
}
WRITERS = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-luna", "gpt-6-sol", "gemini-3.8-flash"]
JUDGE = "claude-opus-5"
#: Probe constants: senses per tier, longest headword (in words), attempts per call, and the
#: requested document word band.
PER_TIER = 2
MAX_HEADWORD_WORDS = 3
MAX_ATTEMPTS = 3
DOC_WORDS = (230, 330)
HTTP_OK = 200
STEM_MIN = 4
STEM_SLACK = 3

PURPOSES = {
    "misconception": (
        "a misconception-correction passage: open with a specific, plausible mistaken belief "
        "a learner might hold about the concept, then correct it using only the source facts"
    ),
    "scenario": (
        "a worked practical scenario: a concrete situation in which someone encounters or "
        "uses the concept, walked through step by step, using only the source facts"
    ),
}

DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_facts_used": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "source_facts_used"],
    "additionalProperties": False,
}
QUERY_STYLES = {
    "troubleshooting": "a troubleshooting or decision question from someone facing a real problem",
    "irrelevant_detail": "a conversational question that includes one or two plausible but "
    "irrelevant personal details",
    "novice_terse": "a terse, underspecified search-box query a novice would type",
    "expert": "a precise question an expert practitioner would ask",
}
QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "enum": list(QUERY_STYLES)},
                    "text": {"type": "string"},
                },
                "required": ["style", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    """Lowercase word tokens."""
    return _WORD.findall(text.casefold())


def leaks(text: str, headword: str) -> bool:
    """Whether the headword (or a simple inflection of its last word) appears."""
    hw = words(headword)
    if not hw:
        return False
    toks = words(text)
    joined = f" {' '.join(toks)} "
    if f" {' '.join(hw)} " in joined:
        return True
    stem = hw[-1]
    if len(hw) == 1 and len(stem) >= STEM_MIN:
        return any(t.startswith(stem) and len(t) - len(stem) <= STEM_SLACK for t in toks)
    return False


def pick_senses(n_per_tier: int = PER_TIER) -> list[dict[str, Any]]:
    """Choose fixed senses: two per tier, distinct domains, with a neutral encyclopedia."""
    senses = ds.dataset(HF / "opengloss-v2.3-senses/data").to_table(
        columns=[
            "sense_id",
            "lexeme_id",
            "headword",
            "pos",
            "tier",
            "domain_root",
            "gloss",
            "sense_index",
            "examples",
        ]
    )
    senses = senses.filter(pc.equal(senses["sense_index"], 0))
    enc = ds.dataset(HF / "opengloss-v2.3-encyclopedia/data/encyclopedia").to_table(
        columns=["lexeme_id", "reading_level", "text"]
    )
    enc = enc.filter(pc.equal(enc["reading_level"], "neutral"))
    enc_by = dict(zip(enc["lexeme_id"].to_pylist(), enc["text"].to_pylist(), strict=True))
    rows = senses.to_pylist()
    rng = random.Random(SEED)  # noqa: S311 - deterministic sampling
    rng.shuffle(rows)
    chosen: list[dict[str, Any]] = []
    for tier in ("core", "tier2", "tier3", "tier4", "tier5", "tier6"):
        used_domains: set[str] = {r["domain_root"] for r in chosen}
        for r in rows:
            if sum(c["tier"] == tier for c in chosen) >= n_per_tier:
                break
            if r["tier"] != tier or r["domain_root"] in used_domains:
                continue
            text = enc_by.get(r["lexeme_id"])
            if not text or len(words(r["headword"])) > MAX_HEADWORD_WORDS:
                continue
            r["encyclopedia"] = " ".join(text.split()[:450])
            r["example"] = (r["examples"] or [{"text": ""}])[0]["text"]
            chosen.append(r)
            used_domains.add(r["domain_root"])
    for i, r in enumerate(chosen):
        r["purpose"] = "misconception" if i % 2 == 0 else "scenario"
        r.pop("examples")
    return chosen


def source_block(s: dict[str, Any]) -> str:
    """The grounding material every writer sees."""
    return (
        f"CONCEPT (do not name it): {s['headword']} ({s['pos']})\n"
        f"DEFINITION: {s['gloss']}\n"
        f"EXAMPLE OF USE: {s['example']}\n"
        f"REFERENCE TEXT:\n{s['encyclopedia']}\n"
    )


def doc_prompt(s: dict[str, Any]) -> str:
    """Task doc."""
    return (
        "You write training text for a semantic search model.\n\n"
        f"{source_block(s)}\n"
        f"Write {PURPOSES[s['purpose']]}. Length: 230-330 words, in flowing prose paragraphs "
        "(no headings, no bullet lists). Never use the concept's name, any inflection of it, "
        "or an obvious synonym-label for it; describe it instead. Use only facts stated in the "
        "source above; do not add outside facts, dates, names or numbers. Begin with a concrete "
        "sentence, not a generic framing. In source_facts_used, list each source fact you relied "
        "on as a short paraphrase."
    )


def query_prompt(s: dict[str, Any]) -> str:
    """Task queries."""
    styles = "\n".join(f"- {k}: {v}" for k, v in QUERY_STYLES.items())
    return (
        "You write realistic search queries for training a semantic search model.\n\n"
        f"{source_block(s)}\n"
        "Write exactly one query for each style below. Each query must be an information need "
        "that this concept (in this sense) answers, without naming the concept, any inflection "
        "of it, or a synonym-label. Sound like a real person, not a quiz.\n"
        f"{styles}"
    )


async def call_openai(
    client: httpx.AsyncClient, model: str, prompt: str, schema: dict, name: str
) -> dict[str, Any]:
    """One Responses API call with strict JSON schema output on the flex tier."""
    body = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": "medium"},
        "service_tier": "flex",
        "max_output_tokens": 8192,
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
    }
    r = await client.post(
        "https://api.openai.com/v1/responses",
        json=body,
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        timeout=900,
    )
    data = r.json()
    if r.status_code != HTTP_OK:
        return {"error": f"{r.status_code}: {json.dumps(data)[:400]}"}
    text = "".join(
        c.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for c in item.get("content", [])
    )
    u = data.get("usage", {})
    return {
        "raw": text,
        "input_tokens": u.get("input_tokens", 0),
        "cached_input_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        "service_tier": data.get("service_tier"),
        "resolved_model": data.get("model"),
    }


def _gemini_schema(schema: dict) -> dict:
    """Gemini's responseSchema dialect: drop additionalProperties."""
    out = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in out:
        out["properties"] = {k: _gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _gemini_schema(out["items"])
    return out


async def call_gemini(
    client: httpx.AsyncClient, model: str, prompt: str, schema: dict, name: str
) -> dict[str, Any]:
    """One generateContent call with a response schema (standard tier)."""
    del name
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
            "maxOutputTokens": 8192,
        },
    }
    r = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": os.environ["GEMINI_API_KEY"]},
        json=body,
        timeout=600,
    )
    data = r.json()
    if r.status_code != HTTP_OK:
        return {"error": f"{r.status_code}: {json.dumps(data)[:400]}"}
    parts = data["candidates"][0].get("content", {}).get("parts", [])
    u = data.get("usageMetadata", {})
    return {
        "raw": "".join(p.get("text", "") for p in parts if not p.get("thought")),
        "input_tokens": u.get("promptTokenCount", 0),
        "cached_input_tokens": u.get("cachedContentTokenCount", 0),
        "output_tokens": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
        "reasoning_tokens": u.get("thoughtsTokenCount", 0),
        "service_tier": "standard",
        "resolved_model": data.get("modelVersion"),
        "finish_reason": data["candidates"][0].get("finishReason"),
    }


async def call_anthropic(client: httpx.AsyncClient, prompt: str) -> dict[str, Any]:
    """One judge call."""
    r = await client.post(
        "https://api.anthropic.com/v1/messages",
        json={
            "model": JUDGE,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        timeout=600,
    )
    data = r.json()
    if r.status_code != HTTP_OK:
        return {"error": f"{r.status_code}: {json.dumps(data)[:400]}"}
    u = data["usage"]
    return {
        "raw": "".join(b.get("text", "") for b in data["content"] if b["type"] == "text"),
        "input_tokens": u["input_tokens"],
        "cached_input_tokens": u.get("cache_read_input_tokens", 0),
        "output_tokens": u["output_tokens"],
    }


def cost(model: str, res: dict[str, Any], *, half: bool = False) -> float:
    """USD for one call; ``half`` prices it at batch/flex rates."""
    i, c, o = RATES[model]
    fresh = res.get("input_tokens", 0) - res.get("cached_input_tokens", 0)
    usd = (
        fresh * i + res.get("cached_input_tokens", 0) * c + res.get("output_tokens", 0) * o
    ) / 1e6
    return usd / 2 if half else usd


def check(task: str, s: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic acceptance checks."""
    if parsed is None:
        return {"schema_ok": False}
    if task == "doc":
        text = parsed.get("text", "")
        n = len(words(text))
        return {
            "schema_ok": True,
            "words": n,
            "length_ok": DOC_WORDS[0] <= n <= DOC_WORDS[1],
            "headword_leak": leaks(text, s["headword"]),
            "has_list_or_heading": bool(re.search(r"^\s*(#|[-*] |\d+\. )", text, re.M)),
        }
    qs = parsed.get("queries", [])
    styles = sorted(q.get("style") for q in qs)
    return {
        "schema_ok": True,
        "n_queries": len(qs),
        "styles_complete": styles == sorted(QUERY_STYLES),
        "headword_leaks": sum(leaks(q.get("text", ""), s["headword"]) for q in qs),
        "mean_words": round(sum(len(words(q.get("text", ""))) for q in qs) / max(1, len(qs)), 1),
    }


JUDGE_PROMPT = """You are grading anonymous candidate outputs for a data-generation task. The \
writers are unknown to you. Judge strictly and independently.

SOURCE MATERIAL (the only facts the writers were allowed to use):
{source}

TASK GIVEN TO EVERY WRITER:
{task}

CANDIDATES:
{candidates}

For EACH candidate, score 1-5 on:
- grounding: 5 = every factual claim is supported by the source; deduct for any unsupported \
or contradicted claim (outside dates, names, numbers, invented facts).
- task_fidelity: does it do exactly the requested purpose/styles, respect length/format, and \
avoid naming the concept?
- naturalness: reads like a skilled human wrote it for a real reader; deduct for filler, \
synthetic stock phrasing, quiz-like or templated wording.
- retrieval_value: how useful it is as training text for a semantic search model that must map \
varied natural language onto this exact concept sense (specific, unambiguous, discriminative).
Also give unsupported_claims: an integer count of claims not supported by the source.

Reply with ONLY a JSON object mapping each candidate letter to \
{{"grounding":int,"task_fidelity":int,"naturalness":int,"retrieval_value":int,\
"unsupported_claims":int,"note":"<=20 words"}}."""


def parse_json(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating a fenced block."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def main() -> None:
    """Run the probe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    senses = pick_senses()
    sem = asyncio.Semaphore(12)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:

        async def run(s: dict[str, Any], task: str, model: str) -> None:
            prompt = doc_prompt(s) if task == "doc" else query_prompt(s)
            schema = DOC_SCHEMA if task == "doc" else QUERY_SCHEMA
            fn = call_gemini if model.startswith("gemini") else call_openai
            async with sem:
                t0 = time.time()
                res = {}
                for attempt in range(MAX_ATTEMPTS):
                    res = await fn(client, model, prompt, schema, task)
                    if "error" not in res or not res["error"].startswith(("429", "408", "5")):
                        break
                    await asyncio.sleep(10 * (attempt + 1))
                res["latency_s"] = round(time.time() - t0, 1)
            parsed = None if "error" in res else parse_json(res["raw"])
            res["checks"] = check(task, s, parsed)
            res["parsed"] = parsed
            if "error" not in res:
                res["cost_usd_standard"] = round(cost(model, res), 6)
                res["cost_usd_half_rate"] = round(cost(model, res, half=True), 6)
            results.append({"sense_id": s["sense_id"], "task": task, "model": model, **res})

        await asyncio.gather(
            *(run(s, t, m) for s in senses for t in ("doc", "queries") for m in WRITERS)
        )

        judgements: list[dict[str, Any]] = []
        rng = random.Random(SEED)  # noqa: S311 - deterministic sampling

        async def judge(s: dict[str, Any], task: str) -> None:
            items = [
                r
                for r in results
                if r["sense_id"] == s["sense_id"] and r["task"] == task and r.get("parsed")
            ]
            rng_local = random.Random(f"{SEED}:{s['sense_id']}:{task}")  # noqa: S311 - deterministic sampling
            rng_local.shuffle(items)
            letters = "ABCDEFG"
            blind = {letters[i]: r["model"] for i, r in enumerate(items)}
            cands = []
            for i, r in enumerate(items):
                body = (
                    r["parsed"]["text"]
                    if task == "doc"
                    else "\n".join(f"[{q['style']}] {q['text']}" for q in r["parsed"]["queries"])
                )
                cands.append(f"--- Candidate {letters[i]} ---\n{body}")
            prompt = JUDGE_PROMPT.format(
                source=source_block(s),
                task=doc_prompt(s) if task == "doc" else query_prompt(s),
                candidates="\n\n".join(cands),
            )
            async with sem:
                res = await call_anthropic(client, prompt)
            scores = parse_json(res.get("raw", "")) or {}
            judgements.append(
                {
                    "sense_id": s["sense_id"],
                    "task": task,
                    "blind_map": blind,
                    "scores": {blind[k]: v for k, v in scores.items() if k in blind},
                    "cost_usd": round(cost(JUDGE, res), 6) if "error" not in res else None,
                    "error": res.get("error"),
                }
            )

        del rng
        await asyncio.gather(*(judge(s, t) for s in senses for t in ("doc", "queries")))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "seed": SEED,
                "rates_per_million": RATES,
                "writers": WRITERS,
                "judge": JUDGE,
                "senses": senses,
                "results": results,
                "judgements": judgements,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
