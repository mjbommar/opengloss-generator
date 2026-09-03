"""Rank the lexicon and cut a core subset. Zero model calls.

Two internal signals (Wikipedia frequency, relation in-degree) and two external ones
(OpenSubtitles spoken frequency, Google web frequency) are combined by mean percentile
rank. Candidates must be single-token, non-stopword, and attested in at least one
independent word list; entries with high in-degree but negligible usage are dropped as
hypernym-slot artifacts ("descriptive term"). See docs/CORE-10K.md.

Usage:
    uv run --with polars python scripts/core_lexicon.py \
        --shards '/nas4/data/workspace/curriculum/data/opengloss_v1_3_candidate_upload/dictionary/*.jsonl' \
        --refs data/core --out data/core --n 10000
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import polars as pl

_SLUG = r"[^a-z0-9]+"


def load_light(shards: str, cache: Path) -> pl.DataFrame:
    """Project the columns the ranking needs and compute in-degree; cache to parquet."""
    if cache.exists():
        return pl.read_parquet(cache)
    files = sorted(glob.glob(shards))
    lf = pl.scan_ndjson(files, infer_schema_length=200)
    light = lf.select(
        "word", "wiki_frequency", "total_senses", "total_edges", "is_stopword",
        "parts_of_speech", "has_encyclopedia",
        "all_synonyms", "all_antonyms", "all_hypernyms", "all_hyponyms",
    ).collect()
    targets = (
        light.select(
            "word",
            pl.concat_list(["all_synonyms", "all_antonyms", "all_hypernyms", "all_hyponyms"]).alias("t"),
        )
        .explode("t")
        .drop_nulls("t")
        .with_columns(
            pl.col("t").str.to_lowercase().str.replace_all(_SLUG, "_").str.strip_chars("_").alias("tid")
        )
        .filter(pl.col("tid") != "")
        .unique(["word", "tid"])
    )
    indeg = targets.group_by("tid").agg(pl.len().alias("in_degree"))
    light = (
        light.with_columns(
            pl.col("word").str.to_lowercase().str.replace_all(_SLUG, "_").str.strip_chars("_").alias("wid")
        )
        .join(indeg, left_on="wid", right_on="tid", how="left")
        .with_columns(pl.col("in_degree").fill_null(0))
        .drop("all_synonyms", "all_antonyms", "all_hypernyms", "all_hyponyms")
    )
    light.write_parquet(cache)
    return light


def load_refs(refs: Path) -> tuple[set[str], dict[str, int], dict[str, int], set[str]]:
    """Return (wamerican, google-10k rank, subtitles rank, popular-25k)."""
    wam = {w.strip().lower() for w in Path("/usr/share/dict/words").read_text().splitlines() if "'" not in w}
    g10k = [w.strip().lower() for w in (refs / "google-10000-english-usa-no-swears.txt").read_text().splitlines() if w.strip()]
    subs: dict[str, int] = {}
    for line in (refs / "en_50k.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isalpha():
            subs.setdefault(parts[0].lower(), len(subs) + 1)
    popular = {w.strip().lower() for w in (refs / "popular.txt").read_text().splitlines() if w.strip()}
    return wam, {w: i + 1 for i, w in enumerate(g10k)}, subs, popular


def rank(light: pl.DataFrame, refs: Path) -> pl.DataFrame:
    """Return every candidate, sorted by composite score (best first)."""
    wam, g10k, subs, popular = load_refs(refs)
    g10k_df = pl.DataFrame({"w": list(g10k), "g10k_rank": list(g10k.values())})
    subs_df = pl.DataFrame({"w": list(subs), "subs_rank": list(subs.values())})
    df = (
        light.with_columns(pl.col("word").str.to_lowercase().alias("w"))
        .join(g10k_df, on="w", how="left")
        .join(subs_df, on="w", how="left")
        .with_columns(
            pl.col("w").is_in(wam).alias("in_wam"),
            pl.col("w").is_in(popular).alias("in_popular"),
            pl.col("w").str.contains(r"^[a-z][a-z\-]{1,14}$").alias("single_token"),
        )
    )
    cand = df.filter(~pl.col("is_stopword") & pl.col("single_token"))
    cand = cand.filter(
        pl.col("in_wam") | pl.col("g10k_rank").is_not_null() | pl.col("subs_rank").is_not_null() | pl.col("in_popular")
    )
    cand = cand.filter(
        ~((pl.col("in_degree") > 500) & (pl.col("wiki_frequency") < 1000) & pl.col("g10k_rank").is_null())
    )
    n = len(cand)

    def pct(col: str, *, descending: bool) -> pl.Expr:
        return pl.col(col).rank(method="average", descending=descending) / n

    return (
        cand.with_columns(
            pct("wiki_frequency", descending=True).alias("p_wiki"),
            pct("in_degree", descending=True).alias("p_indeg"),
            pl.when(pl.col("subs_rank").is_not_null()).then(pct("subs_rank", descending=False)).otherwise(1.0).alias("p_subs"),
            pl.when(pl.col("g10k_rank").is_not_null()).then(pct("g10k_rank", descending=False)).otherwise(1.0).alias("p_g10k"),
        )
        .with_columns(((pl.col("p_wiki") + pl.col("p_indeg") + pl.col("p_subs") + pl.col("p_g10k")) / 4).alias("score"))
        .sort("score")
    )


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shards", required=True)
    ap.add_argument("--refs", type=Path, default=Path("data/core"))
    ap.add_argument("--out", type=Path, default=Path("data/core"))
    ap.add_argument("--n", type=int, default=10_000)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    light = load_light(args.shards, args.out / "light.parquet")
    ranked = rank(light, args.refs)
    core = ranked.head(args.n).with_row_index("rank", offset=1)
    core.select("rank", "word", "score", "wiki_frequency", "in_degree", "subs_rank", "g10k_rank", "total_senses", "parts_of_speech").write_parquet(args.out / f"core_{args.n}.parquet")
    core.select("rank", "word", "wiki_frequency", "in_degree", "total_senses").write_csv(args.out / f"core_{args.n}.tsv", separator="\t")
    print(f"candidates={len(ranked)} core={len(core)} -> {args.out}")  # noqa: T201


if __name__ == "__main__":
    main()
