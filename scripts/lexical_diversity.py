"""Lexical-diversity measures per writer for the pilot (D-63): TTR, distinct-4-grams,
sentence-opener concentration, and near-copy rate against the canonical gloss.

Reads the JSONL files ``collect_writer_texts.py`` produces. No model calls, no extra
dependencies.

Usage:
    uv run python scripts/lexical_diversity.py --input data/writer-texts-*.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from opengloss_generator.hygiene import is_near_copy

_WORD_RE = re.compile(r"[A-Za-z']+")
_OPENING_WORDS = 3


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _opening(text: str) -> str:
    return " ".join(_tokenize(text)[:_OPENING_WORDS])


def _measure(rows: list[dict]) -> dict[str, object]:
    all_tokens: list[str] = []
    fourgrams: Counter[tuple[str, ...]] = Counter()
    openers: Counter[str] = Counter()
    near_copy = 0
    for row in rows:
        tokens = _tokenize(row["text"])
        all_tokens.extend(tokens)
        for i in range(len(tokens) - 3):
            fourgrams[tuple(tokens[i : i + 4])] += 1
        openers[_opening(row["text"])] += 1
        if is_near_copy(row["text"], row["canonical_gloss"]):
            near_copy += 1

    n_sentences = len(rows)
    total_fourgrams = sum(fourgrams.values())
    top_opener_count, top_opener = (
        openers.most_common(1)[0][::-1] if openers else (0, "")
    )
    return {
        "n_sentences": n_sentences,
        "n_tokens": len(all_tokens),
        "type_token_ratio": len(set(all_tokens)) / len(all_tokens) if all_tokens else 0.0,
        "distinct_4gram_rate": len(fourgrams) / total_fourgrams if total_fourgrams else 0.0,
        "top_sentence_opener": top_opener,
        "top_opener_share": top_opener_count / n_sentences if n_sentences else 0.0,
        "n_distinct_openers": len(openers),
        "near_copy_rate": near_copy / n_sentences if n_sentences else 0.0,
    }


def main() -> None:
    """Print per-writer and pooled-vs-luna-alone lexical-diversity measures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    by_writer: dict[str, list[dict]] = {}
    for path in args.input:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            by_writer.setdefault(row["writer"], []).append(row)

    report = {writer: _measure(rows) for writer, rows in by_writer.items()}
    pooled_all = [row for rows in by_writer.values() for row in rows]
    report["__pooled_all_writers__"] = _measure(pooled_all)
    if "gpt-5.6-luna" in by_writer:
        report["__luna_alone__"] = _measure(by_writer["gpt-5.6-luna"])

    print(json.dumps(report, indent=2))
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
