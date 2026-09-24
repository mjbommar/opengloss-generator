r"""G0 census of the immutable OpenGloss v2.3 export (ENCODER-DATA-EXPANSION-PLAN.md § 7, G0).

Zero model calls. Reads the local parquet release under ``data/hf`` (optionally verifying
every file against the published Hub LFS hashes) and writes one machine-readable census:

* inventory: rows, bytes and SHA-256 per file, and whether each matches the Hub;
* coverage: live lexemes/senses by tier, kind, domain, POS; per-sense supervision counts;
  register/reading-level crossing; connected components of the resolved sense graph;
* text families: rows, exact normalized duplicates, student-tokenizer tokens, length
  bands, truncated attended tokens and unique non-overlapping windows at 256/512/1,024,
  MinHash near-duplicates for long families, headword presence, and matched-sample
  diversity (distinct n-grams, sentence-opener entropy, 4-gram concentration), overall
  and per stratum, with tokens by tier and domain;
* supervision: query styles and headword-free compliance, QA type/difficulty, listwise
  grade cells, triple negative kinds, pair kinds;
* effective information: exact text reuse across the training exports and per-sense
  exposure concentration;
* writers: prose-stage provenance by model and writer/tier/domain mutual information.

Nominal tokens are always reported beside unique tokens, per plan § 6.4.

Usage:
    uv sync --extra census --extra hf
    uv run python scripts/census_v23.py --verify-hub \\
        --out reports/census-v2.3/census.json
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import tokenizers
from tokenizers import Tokenizer

#: Read from the environment so pool workers (which re-import this module) see the same
#: values as the parent; `--release` / `--hf-root` set these before the pool starts.
RELEASE = os.environ.get("OPENGLOSS_CENSUS_RELEASE", "v2.3")
HF_ROOT = Path(os.environ.get("OPENGLOSS_CENSUS_HF_ROOT", "data/hf"))
DEFAULT_TOKENIZER = Path("../opengloss-embedding/data/tokenizer/ogbert-tokenizer-16384.json")
CONTEXTS = (256, 512, 1024)
LENGTH_BANDS = (64, 128, 256, 512, 1024, 2048)
BATCH_ROWS = 20_000

#: Matched-sample sizes for diversity measures (words for n-grams, rows for openers).
SAMPLE_WORDS = 200_000
SAMPLE_OPENERS = 20_000
STRATUM_SAMPLE_WORDS = 50_000
STRATUM_SAMPLE_OPENERS = 5_000
MIN_STRATUM_ROWS = 500

#: MinHash: token 8-gram shingles, 64 hashes, 16 bands of 4; a pair is a near-duplicate
#: when at least 80% of the signature agrees with its band bucket's first member.
SHINGLE = 8
N_HASH = 64
BANDS = 16
NEAR_DUP_AGREEMENT = 0.8
MIN_CLUSTER = 2
#: A text reused more often than this counts as heavily reused.
HEAVY_REUSE = 100

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_WS_RE = re.compile(r"\s+")
_LIST_MARKER_RE = re.compile(r"^(?:\d+\.\s+|[-*]\s+)")


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def dataset_path(name: str, sub: str = "data") -> Path:
    """Return the local directory of one v2.3 dataset (or sub-config)."""
    return HF_ROOT / f"opengloss-{RELEASE}-{name}" / sub


def load(name: str, columns: list[str], sub: str = "data") -> pa.Table:
    """Load selected columns of one v2.3 dataset as a single table."""
    return ds.dataset(dataset_path(name, sub), format="parquet").to_table(columns=columns)


def normalize(text: str) -> str:
    """Casefold and collapse whitespace: the key for exact normalized duplicates."""
    return _WS_RE.sub(" ", text.casefold()).strip()


def digest(text: str) -> int:
    """Return a stable 64-bit hash of ``text``."""
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "little")


def words(text: str) -> list[str]:
    """Return the lowercase word tokens used by every word-level measure."""
    return _WORD_RE.findall(text.casefold())


def quantiles(values: np.ndarray) -> dict[str, float]:
    """Return mean, median, p90, p99 and max of ``values``."""
    if values.size == 0:
        return {}
    qs = np.quantile(values, [0.5, 0.9, 0.99])
    return {
        "mean": round(float(values.mean()), 3),
        "p50": float(qs[0]),
        "p90": float(qs[1]),
        "p99": float(qs[2]),
        "max": float(values.max()),
    }


def entropy_bits(counts: Counter[Any] | dict[Any, int]) -> float:
    """Return the Shannon entropy of a count distribution, in bits."""
    total = sum(counts.values())
    if not total:
        return 0.0
    return -sum(c / total * math.log2(c / total) for c in counts.values() if c)


def mutual_information(pairs: Counter[tuple[Any, Any]]) -> dict[str, float]:
    """Return I(left; right) in bits for a joint count table, plus both entropies."""
    total = sum(pairs.values())
    left: Counter[Any] = Counter()
    right: Counter[Any] = Counter()
    for (a, b), c in pairs.items():
        left[a] += c
        right[b] += c
    mi = sum(
        c / total * math.log2((c / total) / ((left[a] / total) * (right[b] / total)))
        for (a, b), c in pairs.items()
        if c
    )
    h_left, h_right = entropy_bits(left), entropy_bits(right)
    return {
        "mi_bits": round(mi, 6),
        "h_left_bits": round(h_left, 6),
        "h_right_bits": round(h_right, 6),
        "normalized_by_min_entropy": round(mi / min(h_left, h_right), 6)
        if min(h_left, h_right) > 0
        else 0.0,
    }


def gini(values: np.ndarray) -> float:
    """Return the Gini coefficient of non-negative ``values`` (0 = perfectly even)."""
    if values.size == 0 or values.sum() == 0:
        return 0.0
    v = np.sort(values.astype(np.float64))
    n = v.size
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


# --------------------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(verify_hub: bool) -> dict[str, Any]:
    """Hash every exported file and, optionally, compare with the Hub's LFS hashes."""
    repos: dict[str, Any] = {}
    for repo_dir in sorted(HF_ROOT.glob(f"opengloss-{RELEASE}-*")):
        files = sorted(repo_dir.rglob("*.parquet")) + sorted(repo_dir.rglob("*.trec"))
        entries = {}
        for f in files:
            rel = f.relative_to(repo_dir).as_posix()
            entry: dict[str, Any] = {"bytes": f.stat().st_size, "sha256": sha256_file(f)}
            if f.suffix == ".parquet":
                entry["rows"] = pq.ParquetFile(f).metadata.num_rows
            entries[rel] = entry
        repos[repo_dir.name] = {"files": entries}
    if verify_hub:
        from huggingface_hub import HfApi  # noqa: PLC0415 - the optional `hf` extra

        api = HfApi()
        for name, info in repos.items():
            repo_id = f"mjbommar/{name}"
            revision = api.dataset_info(repo_id).sha
            hub = {
                item.path: item.lfs.sha256 if item.lfs else None
                for item in api.list_repo_tree(
                    repo_id, repo_type="dataset", recursive=True, revision=revision, expand=True
                )
                if hasattr(item, "lfs")
            }
            info["hub_revision"] = revision
            mismatched = [
                rel for rel, e in info["files"].items() if hub.get(rel) not in (e["sha256"], None)
            ]
            missing_local = sorted(
                p for p in hub if p.endswith(".parquet") and p not in info["files"]
            )
            missing_hub = sorted(p for p in info["files"] if p not in hub)
            info["hub_match"] = not (mismatched or missing_local or missing_hub)
            info["hub_mismatched"] = mismatched
            info["hub_missing_locally"] = missing_local
            info["local_missing_on_hub"] = missing_hub
    totals = {
        "repos": len(repos),
        "files": sum(len(r["files"]) for r in repos.values()),
        "bytes": sum(e["bytes"] for r in repos.values() for e in r["files"].values()),
    }
    if verify_hub:
        totals["all_repos_match_hub"] = all(r["hub_match"] for r in repos.values())
    return {"totals": totals, "repos": repos}


# --------------------------------------------------------------------------------------
# Coverage, register crossing and graph components
# --------------------------------------------------------------------------------------


def counts_of(table: pa.Table, cols: list[str]) -> list[dict[str, Any]]:
    """Return row counts grouped by ``cols``, largest first."""
    g = table.group_by(cols).aggregate([([], "count_all")])
    return g.sort_by([("count_all", "descending")]).to_pylist()


def coverage() -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Census live lexemes/senses and per-sense supervision; return domain maps too."""
    lex = load(
        "lexicon",
        ["lexeme_id", "kind", "entity_type", "tier", "source", "retired", "n_live_senses"],
    )
    live_lex = lex.filter(pc.invert(lex["retired"]))
    senses = load(
        "senses",
        [
            "sense_id",
            "lexeme_id",
            "sense_index",
            "tier",
            "upos",
            "domain_root",
            "source",
            "gloss_renditions",
            "n_examples",
            "n_relations",
            "n_queries",
            "n_qa_pairs",
        ],
    )
    sense_domain = dict(
        zip(
            senses["sense_id"].to_pylist(),
            [d or "none" for d in senses["domain_root"].to_pylist()],
            strict=True,
        )
    )
    lexeme_domain: dict[str, str] = {}
    first_index: dict[str, int] = {}
    for lid, idx, dom in zip(
        senses["lexeme_id"].to_pylist(),
        senses["sense_index"].to_pylist(),
        senses["domain_root"].to_pylist(),
        strict=True,
    ):
        if lid not in first_index or idx < first_index[lid]:
            first_index[lid] = idx
            lexeme_domain[lid] = dom or "none"

    per_sense: dict[str, Any] = {}
    tiers = senses["tier"].to_pylist()
    for col in ("n_examples", "n_relations", "n_queries", "n_qa_pairs"):
        arr = senses[col].to_numpy(zero_copy_only=False).astype(np.int64)
        by_tier: dict[str, float] = {}
        for tier in sorted(set(tiers)):
            mask = np.array([t == tier for t in tiers])
            by_tier[tier] = round(float((arr[mask] == 0).mean()), 6)
        per_sense[col] = {
            **quantiles(arr),
            "zero_share": round(float((arr == 0).mean()), 6),
            "zero_share_by_tier": by_tier,
        }

    # Register / reading-level crossing of gloss renditions, per sense.
    n_registers = Counter()
    n_levels = Counter()
    register_by_tier: Counter[tuple[str, bool]] = Counter()
    register_by_domain: Counter[tuple[str, bool]] = Counter()
    for sid, tier, rends in zip(
        senses["sense_id"].to_pylist(), tiers, senses["gloss_renditions"].to_pylist(), strict=True
    ):
        regs = {r["register"] for r in rends if r["register"] != "plain"}
        levels = {r["reading_level"] for r in rends if r["reading_level"] != "neutral"}
        n_registers[len(regs)] += 1
        n_levels[len(levels)] += 1
        register_by_tier[(tier, bool(regs))] += 1
        register_by_domain[(sense_domain[sid], bool(regs))] += 1

    def share_true(counter: Counter[tuple[str, bool]]) -> dict[str, float]:
        keys = sorted({k for k, _ in counter})
        return {
            k: round(counter[(k, True)] / (counter[(k, True)] + counter[(k, False)]), 6)
            for k in keys
        }

    result = {
        "lexemes": {
            "total_rows": lex.num_rows,
            "live": live_lex.num_rows,
            "by_kind": counts_of(live_lex, ["kind"]),
            "by_entity_type": counts_of(live_lex, ["entity_type"]),
            "by_tier": counts_of(live_lex, ["tier"]),
            "by_source": counts_of(live_lex, ["source"]),
            "live_with_zero_live_senses": int(
                pc.sum(pc.equal(live_lex["n_live_senses"], 0)).as_py() or 0
            ),
        },
        "senses": {
            "live": senses.num_rows,
            "by_tier": counts_of(senses, ["tier"]),
            "by_domain_root": counts_of(senses, ["domain_root"]),
            "by_upos": counts_of(senses, ["upos"]),
            "by_source": counts_of(senses, ["source"]),
        },
        "per_sense_supervision": per_sense,
        "gloss_rendition_crossing": {
            "distinct_non_plain_registers_per_sense": dict(sorted(n_registers.items())),
            "distinct_non_neutral_levels_per_sense": dict(sorted(n_levels.items())),
            "share_with_any_register_variant_by_tier": share_true(register_by_tier),
            "share_with_any_register_variant_by_domain": share_true(register_by_domain),
            "register_variant_vs_tier_mi": mutual_information(register_by_tier),
            "register_variant_vs_domain_mi": mutual_information(register_by_domain),
        },
    }
    return result, sense_domain, lexeme_domain


class UnionFind:
    """Disjoint sets over ``range(n)`` with path compression."""

    def __init__(self, n: int) -> None:
        """Start with ``n`` singleton sets."""
        self.parent = np.arange(n, dtype=np.int64)

    def find(self, x: int) -> int:
        """Return the root of ``x``'s set."""
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return int(root)

    def union(self, a: int, b: int) -> None:
        """Merge the sets holding ``a`` and ``b``."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def sizes(self) -> np.ndarray:
        """Return the size of every set."""
        roots = np.array([self.find(i) for i in range(self.parent.size)])
        return np.bincount(roots)[np.unique(roots)]


def size_histogram(sizes: np.ndarray, n: int) -> dict[str, Any]:
    """Summarize component sizes over ``n`` nodes."""
    edges = [(1, 1), (2, 10), (11, 100), (101, 1_000), (1_001, 10_000), (10_001, 10**9)]
    hist = {}
    for lo, hi in edges:
        mask = (sizes >= lo) & (sizes <= hi)
        hist[f"{lo}-{hi}" if hi < 10**9 else f">={lo}"] = {
            "components": int(mask.sum()),
            "members": int(sizes[mask].sum()),
            "member_share": round(float(sizes[mask].sum() / n), 6),
        }
    top = np.sort(sizes)[::-1][:5]
    return {
        "nodes": n,
        "components": int(sizes.size),
        "largest": [int(x) for x in top],
        "largest_share": round(float(top[0] / n), 6),
        "histogram": hist,
    }


def components(sense_domain: dict[str, str]) -> dict[str, Any]:
    """Connected components of the resolved sense graph and of its lexeme projection."""
    rel = load(
        "relations", ["source_sense_id", "target_sense_id", "resolved", "type"], "data/relations"
    )
    rel = rel.filter(rel["resolved"])
    sense_ids = sorted(sense_domain)
    index = {s: i for i, s in enumerate(sense_ids)}
    senses = load("senses", ["sense_id", "lexeme_id"])
    sense_lexeme = dict(
        zip(senses["sense_id"].to_pylist(), senses["lexeme_id"].to_pylist(), strict=True)
    )
    lexeme_ids = sorted(set(sense_lexeme.values()))
    lindex = {lid: i for i, lid in enumerate(lexeme_ids)}
    uf_sense = UnionFind(len(sense_ids))
    uf_lex = UnionFind(len(lexeme_ids))
    uf_lex_taxonomic = UnionFind(len(lexeme_ids))
    used = 0
    for src, dst, typ in zip(
        rel["source_sense_id"].to_pylist(),
        rel["target_sense_id"].to_pylist(),
        rel["type"].to_pylist(),
        strict=True,
    ):
        if src in index and dst in index:
            used += 1
            uf_sense.union(index[src], index[dst])
            a, b = lindex[sense_lexeme[src]], lindex[sense_lexeme[dst]]
            uf_lex.union(a, b)
            if typ in ("synonym", "antonym", "hypernym", "hyponym", "instance_of"):
                uf_lex_taxonomic.union(a, b)
    return {
        "resolved_edges_between_live_senses": used,
        "sense_graph": size_histogram(uf_sense.sizes(), len(sense_ids)),
        "lexeme_graph_senses_merged": size_histogram(uf_lex.sizes(), len(lexeme_ids)),
        "lexeme_graph_taxonomic_edges_only": size_histogram(
            uf_lex_taxonomic.sizes(), len(lexeme_ids)
        ),
        "note": (
            "Sense graph joins live senses by resolved relation edges. The lexeme graphs merge "
            "all senses of a lexeme (the partition unit that keeps a headword's renditions on "
            "one side); the taxonomic variant uses only synonym/antonym/hypernym/hyponym/"
            "instance_of edges."
        ),
    }


# --------------------------------------------------------------------------------------
# Text families
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Family:
    """One text family: a dataset, its text column, strata and optional filter."""

    name: str
    dataset: str
    text: str
    sub: str = "data"
    headword: str | None = "headword"
    sense_col: str | None = "sense_id"
    lexeme_col: str | None = "lexeme_id"
    strata: tuple[str, ...] = ()
    filter_expr: Any = None
    near_dup: bool = False
    extra: tuple[str, ...] = field(default=())


def families() -> list[Family]:
    """Return every text family the census measures."""
    canonical = pc.field("is_canonical")
    plain = pc.field("register") == "plain"
    return [
        Family("gloss.canonical", "definitions", "text", filter_expr=canonical),
        Family(
            "gloss.reading_level",
            "definitions",
            "text",
            strata=("reading_level",),
            filter_expr=~canonical & plain,
        ),
        Family("gloss.register", "definitions", "text", strata=("register",), filter_expr=~plain),
        Family(
            "example.reading_level",
            "examples",
            "text",
            strata=("reading_level", "source"),
            filter_expr=plain,
        ),
        Family("example.register", "examples", "text", strata=("register",), filter_expr=~plain),
        Family(
            "encyclopedia",
            "encyclopedia",
            "text",
            sub="data/encyclopedia",
            sense_col=None,
            strata=("reading_level",),
            near_dup=True,
        ),
        Family(
            "lexical_explanation",
            "encyclopedia",
            "text",
            sub="data/explanation",
            sense_col=None,
            near_dup=True,
        ),
        Family(
            "contrast",
            "contrasts",
            "text",
            headword="source_headword",
            sense_col="source_sense_id",
            lexeme_col="source_lexeme_id",
            strata=("relation_type", "verdict"),
            near_dup=True,
        ),
        Family("etymology", "etymology", "summary", sense_col=None, near_dup=True),
        Family("query", "queries", "text", strata=("style", "headword_free")),
        Family("qa.question", "qa-pairs", "question", strata=("question_type", "difficulty")),
        Family(
            "qa.answer",
            "qa-pairs",
            "answer",
            strata=("question_type", "difficulty"),
            near_dup=True,
        ),
        Family(
            "pretrain",
            "pretrain",
            "text",
            headword=None,
            sense_col=None,
            strata=("template", "level_used"),
            near_dup=True,
        ),
    ]


# Worker globals, set by the pool initializer in each process.
_SENSE_DOMAIN: dict[str, str] = {}
_LEXEME_DOMAIN: dict[str, str] = {}
_TOKENIZER_PATH = ""
_HASH_A: np.ndarray = np.empty(0, dtype=np.uint64)
_HASH_B: np.ndarray = np.empty(0, dtype=np.uint64)
_SHINGLE_POW: np.ndarray = np.empty(0, dtype=np.uint64)


def _init_worker(sense_domain: dict[str, str], lexeme_domain: dict[str, str], tok: str) -> None:
    global _SENSE_DOMAIN, _LEXEME_DOMAIN, _TOKENIZER_PATH
    _SENSE_DOMAIN, _LEXEME_DOMAIN, _TOKENIZER_PATH = sense_domain, lexeme_domain, tok
    _init_hashes()


def _init_hashes() -> None:
    global _HASH_A, _HASH_B, _SHINGLE_POW  # noqa: PLW0603 - per-process worker state
    rng = np.random.default_rng(20260923)
    _HASH_A = rng.integers(1, 2**63, size=N_HASH, dtype=np.uint64) * np.uint64(2) + np.uint64(1)
    _HASH_B = rng.integers(0, 2**63, size=N_HASH, dtype=np.uint64)
    base = np.uint64(1_000_003)
    pow_ = [np.uint64(1)]
    with np.errstate(over="ignore"):
        for _ in range(SHINGLE - 1):
            pow_.append(pow_[-1] * base)
    _SHINGLE_POW = np.array(pow_[::-1], dtype=np.uint64)


def minhash(ids: np.ndarray) -> np.ndarray:
    """Return the MinHash signature of a token-id sequence."""
    ids = ids.astype(np.uint64) + np.uint64(1)
    with np.errstate(over="ignore"):
        if ids.size < SHINGLE:
            sh = np.array([(ids * _SHINGLE_POW[-ids.size :]).sum()], dtype=np.uint64)
        else:
            win = np.lib.stride_tricks.sliding_window_view(ids, SHINGLE)
            sh = (win * _SHINGLE_POW).sum(axis=1)
        sh ^= sh >> np.uint64(29)
        sh = np.unique(sh)
        h = (sh[None, :] * _HASH_A[:, None] + _HASH_B[:, None]) >> np.uint64(32)
    return h.min(axis=1).astype(np.uint32)


def near_duplicates(sigs: np.ndarray, exact_keys: np.ndarray) -> dict[str, Any]:
    """Cluster near-duplicate documents by banded MinHash signatures."""
    n = sigs.shape[0]
    uf = UnionFind(n)
    rows = N_HASH // BANDS
    for band in range(BANDS):
        block = sigs[:, band * rows : (band + 1) * rows].astype(np.uint64)
        key = np.zeros(n, dtype=np.uint64)
        with np.errstate(over="ignore"):
            for j in range(rows):
                key = key * np.uint64(0x9E3779B97F4A7C15) + block[:, j]
        order = np.argsort(key, kind="stable")
        sk = key[order]
        starts = np.flatnonzero(np.r_[True, sk[1:] != sk[:-1]])
        ends = np.r_[starts[1:], n]
        for s, e in zip(starts, ends, strict=True):
            if e - s < MIN_CLUSTER:
                continue
            members = order[s:e]
            first = members[0]
            agree = (sigs[members[1:]] == sigs[first]).mean(axis=1)
            for m in members[1:][agree >= NEAR_DUP_AGREEMENT]:
                uf.union(int(first), int(m))
    roots = np.array([uf.find(i) for i in range(n)])
    near_excess = n - np.unique(roots).size
    exact_excess = n - np.unique(exact_keys).size
    cluster_sizes = np.bincount(roots)
    in_cluster = int((cluster_sizes[roots] >= MIN_CLUSTER).sum())
    return {
        "docs": n,
        "docs_in_near_dup_cluster": in_cluster,
        "docs_in_near_dup_cluster_share": round(in_cluster / n, 6) if n else 0.0,
        "near_dup_excess_share": round(near_excess / n, 6) if n else 0.0,
        "exact_normalized_excess_share": round(exact_excess / n, 6) if n else 0.0,
        "method": (
            f"MinHash over student-token {SHINGLE}-gram shingles, {N_HASH} hashes, "
            f"{BANDS}x{rows} LSH, signature agreement >= {NEAR_DUP_AGREEMENT} to the bucket's "
            "first member; excess = docs removable by keeping one per cluster"
        ),
    }


class Sampler:
    """Bottom-k sample by text hash: deterministic and uniform over distinct texts."""

    def __init__(self, k: int) -> None:
        """Keep the ``k`` texts with the smallest hashes."""
        self.k = k
        self.heap: list[tuple[int, str]] = []  # max-heap via negated hash

    def offer(self, h: int, text: str) -> None:
        """Consider one distinct text for the sample."""
        if len(self.heap) < self.k:
            heapq.heappush(self.heap, (-h, text))
        elif -self.heap[0][0] > h:
            heapq.heapreplace(self.heap, (-h, text))

    def texts(self) -> list[str]:
        """Return the sample in ascending hash order."""
        return [t for _, t in sorted(self.heap, key=lambda x: -x[0])]


def opener(text: str) -> str:
    """Return the first three words of the first non-header line."""
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return " ".join(words(line)[:3])
    return ""


def diversity(texts: list[str], sample_words: int, sample_openers: int) -> dict[str, Any]:
    """Measure distinct n-grams, 4-gram concentration and opener entropy on a sample."""
    ngrams = {n: Counter() for n in (1, 2, 3, 4)}
    total = dict.fromkeys(ngrams, 0)
    used_words = 0
    used_texts = 0
    for text in texts:
        if used_words >= sample_words:
            break
        ws = words(text)
        used_words += len(ws)
        used_texts += 1
        for n, grams in ngrams.items():
            for i in range(len(ws) - n + 1):
                grams[tuple(ws[i : i + n])] += 1
                total[n] += 1
    openers = Counter(opener(t) for t in texts[:sample_openers])
    n_open = sum(openers.values())
    top4 = ngrams[4].most_common(20)
    return {
        "sample_texts": used_texts,
        "sample_words": used_words,
        "distinct_n": {
            str(n): round(len(ngrams[n]) / total[n], 6) if total[n] else 0.0 for n in ngrams
        },
        "top20_4gram_share": round(sum(c for _, c in top4) / total[4], 6) if total[4] else 0.0,
        "top_4grams": [[" ".join(g), c] for g, c in top4[:10]],
        "opener_sample": n_open,
        "opener_entropy_bits": round(entropy_bits(openers), 4),
        "opener_distinct_share": round(len(openers) / n_open, 6) if n_open else 0.0,
        "top_openers": [[o, round(c / n_open, 5)] for o, c in openers.most_common(8)],
    }


def headword_present(text_norm_words: str, headword: str) -> bool:
    """Return whether ``headword`` occurs as whole words in pre-tokenized text."""
    hw = " ".join(words(headword))
    return bool(hw) and f" {hw} " in f" {text_norm_words} "


def run_family(fam: Family) -> dict[str, Any]:  # noqa: PLR0912, PLR0915 - one streaming pass
    """Measure one text family (runs in a worker process)."""
    os.environ.setdefault("RAYON_NUM_THREADS", "4")
    tok = Tokenizer.from_file(_TOKENIZER_PATH)
    t0 = time.time()
    columns = [fam.text, *fam.strata]
    if fam.headword:
        columns.append(fam.headword)
    for c in (fam.sense_col, fam.lexeme_col):
        if c and c not in columns:
            columns.append(c)
    if "tier" not in columns:
        columns.append("tier")
    dset = ds.dataset(dataset_path(fam.dataset, fam.sub), format="parquet")

    seen: set[int] = set()
    lengths: list[np.ndarray] = []
    unique_tokens = 0
    attended = dict.fromkeys(CONTEXTS, 0)
    windows = dict.fromkeys(CONTEXTS, 0)
    window_hashes: dict[int, set[int]] = {c: set() for c in CONTEXTS}
    sigs: list[np.ndarray] = []
    exact_keys: list[int] = []
    strata_rows: Counter[str] = Counter()
    strata_tokens: Counter[str] = Counter()
    tier_tokens: Counter[str] = Counter()
    domain_tokens: Counter[str] = Counter()
    domain_rows: Counter[str] = Counter()
    hw_present = 0
    hw_rows = 0
    hw_by_stratum: Counter[tuple[str, bool]] = Counter()
    sampler = Sampler(max(SAMPLE_OPENERS, 60_000))
    stratum_samplers: dict[str, Sampler] = defaultdict(lambda: Sampler(STRATUM_SAMPLE_OPENERS * 2))
    n_rows = 0
    n_words_total = 0

    for batch in dset.to_batches(columns=columns, filter=fam.filter_expr, batch_size=BATCH_ROWS):
        if batch.num_rows == 0:
            continue
        cols = {c: batch.column(c).to_pylist() for c in columns}
        texts: list[str] = [t or "" for t in cols[fam.text]]
        encs = tok.encode_batch(texts, add_special_tokens=False)
        for i, text in enumerate(texts):
            n_rows += 1
            ids = np.asarray(encs[i].ids, dtype=np.uint32)
            ntok = int(ids.size)
            norm = normalize(text)
            h = digest(norm)
            exact_keys.append(h)
            stratum = "|".join(str(cols[s][i]) for s in fam.strata) if fam.strata else ""
            if h not in seen:
                seen.add(h)
                unique_tokens += ntok
                sampler.offer(h, text)
                if fam.strata:
                    stratum_samplers[stratum].offer(h, text)
            for c in CONTEXTS:
                attended[c] += min(ntok, c)
                nw = max(1, math.ceil(ntok / c))
                windows[c] += nw
                for w in range(nw):
                    window_hashes[c].add(
                        int.from_bytes(
                            hashlib.blake2b(
                                ids[w * c : (w + 1) * c].tobytes(), digest_size=8
                            ).digest(),
                            "little",
                        )
                    )
            lengths.append(ntok)
            if fam.strata:
                strata_rows[stratum] += 1
                strata_tokens[stratum] += ntok
            tier_tokens[str(cols["tier"][i])] += ntok
            if fam.sense_col and cols[fam.sense_col][i] in _SENSE_DOMAIN:
                dom = _SENSE_DOMAIN[cols[fam.sense_col][i]]
            else:
                dom = (
                    _LEXEME_DOMAIN.get(cols[fam.lexeme_col][i], "unmapped")
                    if fam.lexeme_col
                    else "unmapped"
                )
            domain_tokens[dom] += ntok
            domain_rows[dom] += 1
            if fam.headword:
                ws = words(text)
                n_words_total += len(ws)
                present = headword_present(" ".join(ws), cols[fam.headword][i] or "")
                hw_rows += 1
                hw_present += present
                if fam.strata:
                    hw_by_stratum[(stratum, present)] += 1
            if fam.near_dup:
                sigs.append(minhash(ids))
    lens = np.asarray(lengths, dtype=np.int64)
    total_tokens = int(lens.sum())
    result: dict[str, Any] = {
        "dataset": f"opengloss-{RELEASE}-{fam.dataset}/{fam.sub}",
        "text_column": fam.text,
        "rows": n_rows,
        "unique_normalized_texts": len(seen),
        "exact_normalized_duplicate_rows": n_rows - len(seen),
        "exact_normalized_duplicate_share": round((n_rows - len(seen)) / n_rows, 6)
        if n_rows
        else 0.0,
        "tokens": total_tokens,
        "unique_text_tokens": unique_tokens,
        "tokens_per_row": quantiles(lens),
        "length_band_share": {
            f">{b}": round(float((lens > b).mean()), 6) if n_rows else 0.0 for b in LENGTH_BANDS
        },
        "truncated_attended_tokens": {str(c): attended[c] for c in CONTEXTS},
        "windows": {
            str(c): {
                "windows": windows[c],
                "unique_windows": len(window_hashes[c]),
                "unique_share": round(len(window_hashes[c]) / windows[c], 6) if windows[c] else 0.0,
            }
            for c in CONTEXTS
        },
        "tokens_by_tier": dict(tier_tokens.most_common()),
        "tokens_by_domain_root": dict(domain_tokens.most_common()),
        "rows_by_domain_root": dict(domain_rows.most_common()),
        "diversity": diversity(sampler.texts(), SAMPLE_WORDS, SAMPLE_OPENERS),
    }
    if fam.headword:
        result["headword_present_share"] = round(hw_present / hw_rows, 6) if hw_rows else 0.0
        result["words"] = n_words_total
    if fam.strata:
        strata: dict[str, Any] = {}
        for s, rows in strata_rows.most_common():
            entry: dict[str, Any] = {
                "rows": rows,
                "tokens": strata_tokens[s],
                "mean_tokens": round(strata_tokens[s] / rows, 2),
            }
            if fam.headword:
                p = hw_by_stratum[(s, True)]
                entry["headword_present_share"] = round(p / rows, 6)
            if rows >= MIN_STRATUM_ROWS:
                d = diversity(
                    stratum_samplers[s].texts(), STRATUM_SAMPLE_WORDS, STRATUM_SAMPLE_OPENERS
                )
                entry["diversity"] = {
                    k: d[k]
                    for k in ("sample_words", "distinct_n", "opener_entropy_bits", "top_openers")
                }
                entry["diversity"]["top_openers"] = d["top_openers"][:3]
            strata[s] = entry
        result["strata"] = {"columns": list(fam.strata), "values": strata}
    if fam.near_dup and sigs:
        result["near_duplicates"] = near_duplicates(
            np.vstack(sigs), np.asarray(exact_keys, dtype=np.uint64)
        )
    result["seconds"] = round(time.time() - t0, 1)
    _log(f"[census] {fam.name}: {n_rows:,} rows, {total_tokens:,} tokens, {result['seconds']}s")
    return result


# --------------------------------------------------------------------------------------
# Supervision: queries, QA, qrels, triples, pairs
# --------------------------------------------------------------------------------------


def supervision() -> dict[str, Any]:
    """Census queries, QA pairs, listwise qrels, triples and pairs."""
    queries = load("queries", ["sense_id", "style", "headword", "text", "headword_free"])
    leak = Counter()
    for hw, text, free in zip(
        queries["headword"].to_pylist(),
        queries["text"].to_pylist(),
        queries["headword_free"].to_pylist(),
        strict=True,
    ):
        leak[(bool(free), headword_present(" ".join(words(text)), hw or ""))] += 1
    per_sense_q = np.asarray(
        list(Counter(queries["sense_id"].to_pylist()).values()), dtype=np.int64
    )
    q_styles_per_sense = Counter(
        len(v)
        for v in _group_sets(queries["sense_id"].to_pylist(), queries["style"].to_pylist()).values()
    )

    qa = load("qa-pairs", ["sense_id", "question_type", "difficulty", "grounded_in"])
    grounded = np.asarray([len(g or []) for g in qa["grounded_in"].to_pylist()], dtype=np.int64)

    lw = load("qrels", ["query_source", "candidates", "n_candidates"], "data/listwise")
    grade_hist: Counter[int] = Counter()
    cand_kind_grade: Counter[tuple[str, int]] = Counter()
    per_list = Counter()
    grade_profile: Counter[str] = Counter()
    n_cand = []
    for cands in lw["candidates"].to_pylist():
        grades = [c["grade"] for c in cands]
        n_cand.append(len(cands))
        g = Counter(grades)
        for gr, c in g.items():
            grade_hist[gr] += c
        for c in cands:
            kind = "encyclopedia" if c["id"].endswith(":encyclopedia") else "sense_gloss"
            cand_kind_grade[(kind, c["grade"])] += 1
        per_list["multi_grade3"] += g[3] >= MIN_CLUSTER
        per_list["any_grade2"] += g[2] >= 1
        per_list["any_grade1"] += g[1] >= 1
        per_list["all_four_grades"] += all(g[x] >= 1 for x in (0, 1, 2, 3))
        grade_profile["".join(str(x) for x in sorted(g, reverse=True))] += 1
    n_lists = lw.num_rows

    triples = load("retrieval-triples", ["negative_kind", "query_source"])
    pairs = load("retrieval-pairs", ["kind", "label"])
    return {
        "queries": {
            "rows": queries.num_rows,
            "by_style_headword_free": counts_of(queries, ["style", "headword_free"]),
            "per_sense": quantiles(per_sense_q),
            "distinct_styles_per_sense": dict(sorted(q_styles_per_sense.items())),
            "headword_free_flag_vs_observed_headword": {
                f"flag={f},present={p}": c for (f, p), c in sorted(leak.items())
            },
            "headword_free_leak_share": round(
                leak[(True, True)] / (leak[(True, True)] + leak[(True, False)]), 6
            ),
        },
        "qa_pairs": {
            "rows": qa.num_rows,
            "by_type_difficulty": counts_of(qa, ["question_type", "difficulty"]),
            "grounded_in_per_pair": quantiles(grounded),
        },
        "qrels_listwise": {
            "lists": n_lists,
            "by_query_source": counts_of(lw, ["query_source"]),
            "candidates_per_list": quantiles(np.asarray(n_cand, dtype=np.int64)),
            "grade_histogram": {str(k): v for k, v in sorted(grade_hist.items())},
            "candidate_kind_by_grade": {
                f"{k}/{g}": c for (k, g), c in sorted(cand_kind_grade.items())
            },
            "list_share": {k: round(v / n_lists, 6) for k, v in sorted(per_list.items())},
            "grade_set_profiles": dict(grade_profile.most_common(12)),
            "note": "grade 0 is not split into hard and easy roles in v2.3",
        },
        "retrieval_triples": {
            "rows": triples.num_rows,
            "by_negative_kind_query_source": counts_of(triples, ["negative_kind", "query_source"]),
        },
        "retrieval_pairs": {
            "rows": pairs.num_rows,
            "by_kind_label": counts_of(pairs, ["kind", "label"]),
        },
    }


def _group_sets(keys: list[str], values: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for k, v in zip(keys, values, strict=True):
        out[k].add(v)
    return out


# --------------------------------------------------------------------------------------
# Effective information: text reuse across training exports; per-sense exposure
# --------------------------------------------------------------------------------------


def _value_counts(arr: pa.ChunkedArray | pa.Array) -> pa.Table:
    vc = pc.value_counts(arr)
    return pa.table({"text": vc.field("values"), "n": vc.field("counts")})


def reuse_and_exposure() -> dict[str, Any]:
    """Measure exact text reuse across training exports and per-sense exposure."""
    slots: list[pa.Table] = []
    slot_counts: dict[str, int] = {}
    for name, cols, sub in (
        ("retrieval-pairs", ["text_a", "text_b"], "data"),
        ("retrieval-triples", ["query", "positive", "negative"], "data"),
    ):
        for col in cols:
            t = load(name, [col], sub)
            slot_counts[f"{name}.{col}"] = t.num_rows
            slots.append(_value_counts(t[col]))
            del t
    lw = load("qrels", ["query", "candidates"], "data/listwise")
    flat = pc.list_flatten(lw["candidates"])
    cand_text = pc.struct_field(flat, "text")
    slot_counts["qrels.listwise.query"] = lw.num_rows
    slot_counts["qrels.listwise.candidate"] = len(cand_text)
    slots.append(_value_counts(lw["query"]))
    slots.append(_value_counts(cand_text))
    merged = pa.concat_tables(slots).group_by("text").aggregate([("n", "sum")])
    n = merged["n_sum"].to_numpy()
    total_slots = int(n.sum())
    order = np.sort(n)[::-1]
    top1 = order[: max(1, order.size // 100)].sum()
    lens = pc.utf8_length(merged["text"]).to_numpy()
    # Ties at the cut are broken by text so the list is deterministic across runs.
    texts = merged["text"]
    candidates = np.flatnonzero(n >= order[9])
    top_idx = sorted(candidates, key=lambda i: (-int(n[i]), texts[int(i)].as_py()))[:10]
    reuse = {
        "text_slots_by_column": slot_counts,
        "text_slots": total_slots,
        "unique_texts": int(n.size),
        "slots_per_unique_text": round(total_slots / n.size, 3),
        "occurrences_per_unique_text": quantiles(n.astype(np.float64)),
        "slot_share_of_top_1pct_texts": round(float(top1 / total_slots), 6),
        "slot_share_of_texts_used_over_100_times": round(
            float(n[n > HEAVY_REUSE].sum() / total_slots), 6
        ),
        "char_weighted_slots_per_unique_char": round(float((n * lens).sum() / lens.sum()), 3),
        "most_repeated": [[texts[int(i)].as_py()[:90], int(n[i])] for i in top_idx],
    }
    del merged, slots

    exposure: Counter[str] = Counter()
    p = load("retrieval-pairs", ["sense_a", "sense_b"])
    for col in ("sense_a", "sense_b"):
        vc = pc.value_counts(p[col])
        for k, c in zip(
            vc.field("values").to_pylist(), vc.field("counts").to_pylist(), strict=True
        ):
            exposure[k] += c
    del p
    for name, sub in (("retrieval-triples", "data"), ("qrels", "data/listwise")):
        vc = pc.value_counts(load(name, ["sense_id"], sub)["sense_id"])
        for k, c in zip(
            vc.field("values").to_pylist(), vc.field("counts").to_pylist(), strict=True
        ):
            exposure[k] += c
    senses = load("senses", ["sense_id", "tier"])
    tiers = dict(zip(senses["sense_id"].to_pylist(), senses["tier"].to_pylist(), strict=True))
    arr = np.asarray([exposure.get(s, 0) for s in tiers], dtype=np.int64)
    by_tier = {}
    for tier in sorted(set(tiers.values())):
        mask = np.array([t == tier for t in tiers.values()])
        by_tier[tier] = {**quantiles(arr[mask].astype(np.float64)), "senses": int(mask.sum())}
    exposure_summary = {
        "definition": (
            "rows naming the sense in retrieval-pairs (either side), triples and listwise qrels"
        ),
        "per_live_sense": quantiles(arr.astype(np.float64)),
        "zero_exposure_share": round(float((arr == 0).mean()), 6),
        "gini": round(gini(arr), 6),
        "top_1pct_share": round(float(np.sort(arr)[::-1][: arr.size // 100].sum() / arr.sum()), 6),
        "by_tier": by_tier,
    }
    return {"training_export_text_reuse": reuse, "per_sense_exposure": exposure_summary}


def pretrain_verbatim_reuse() -> dict[str, Any]:
    """Share of pretrain lines that reproduce a source paragraph verbatim (after markers)."""
    source: set[int] = set()
    for name, col, sub in (
        ("definitions", "text", "data"),
        ("examples", "text", "data"),
        ("encyclopedia", "text", "data/encyclopedia"),
        ("encyclopedia", "text", "data/explanation"),
        ("contrasts", "text", "data"),
        ("etymology", "summary", "data"),
    ):
        for batch in ds.dataset(dataset_path(name, sub)).to_batches(
            columns=[col], batch_size=100_000
        ):
            for text in batch.column(col).to_pylist():
                for raw in (text or "").split("\n"):
                    para = normalize(raw)
                    if para:
                        source.add(digest(para))
    lines = Counter()
    chars = Counter()
    for batch in ds.dataset(dataset_path("pretrain")).to_batches(
        columns=["text", "template"], batch_size=50_000
    ):
        for text, template in zip(
            batch.column("text").to_pylist(), batch.column("template").to_pylist(), strict=True
        ):
            for line in text.split("\n"):
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    kind = "header"
                else:
                    s = _LIST_MARKER_RE.sub("", s).strip().strip('"“”')
                    kind = "verbatim" if digest(normalize(s)) in source else "composed"
                lines[(template, kind)] += 1
                chars[(template, kind)] += len(line)
    out: dict[str, Any] = {}
    for template in sorted({t for t, _ in chars}):
        tot = sum(v for (t, _), v in chars.items() if t == template)
        out[template] = {
            k: round(chars[(template, k)] / tot, 6) for k in ("header", "verbatim", "composed")
        }
    tot = sum(chars.values())
    out["all"] = {
        k: round(sum(v for (_, kk), v in chars.items() if kk == k) / tot, 6)
        for k in ("header", "verbatim", "composed")
    }
    return {
        "char_share_by_template": out,
        "note": "verbatim = a pretrain line equal (normalized, after list markers/quotes) to a "
        "paragraph of a source rendition; composed lines (e.g. thesaurus lists, usage-note joins) "
        "are deterministic recombinations of source fields",
    }


# --------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------

PROSE_STAGES = (
    "senses",
    "renditions",
    "examples",
    "encyclopedia",
    "lexical_explanation",
    "queries",
    "qa_pairs",
    "contrasts",
    "etymology",
)


def writers(lexeme_domain: dict[str, str]) -> dict[str, Any]:
    """Summarize prose-stage provenance by model and its dependence on tier/domain."""
    prov = load("provenance", ["lexeme_id", "stage", "model", "cost_usd", "output_tokens", "tier"])
    stage_model = prov.group_by(["stage", "model"]).aggregate(
        [([], "count_all"), ("cost_usd", "sum"), ("output_tokens", "sum")]
    )
    prose = prov.filter(pc.is_in(prov["stage"], pa.array(PROSE_STAGES)))
    per_lexeme: dict[str, set[str]] = _group_sets(
        prose["lexeme_id"].to_pylist(), prose["model"].to_pylist()
    )
    lex = load("lexicon", ["lexeme_id", "tier", "source", "retired"])
    lex = lex.filter(pc.invert(lex["retired"]))
    writer_tier: Counter[tuple[str, str]] = Counter()
    writer_domain: Counter[tuple[str, str]] = Counter()
    writer_source: Counter[tuple[str, str]] = Counter()
    for lid, tier, source in zip(
        lex["lexeme_id"].to_pylist(),
        lex["tier"].to_pylist(),
        lex["source"].to_pylist(),
        strict=True,
    ):
        models = per_lexeme.get(lid, set())
        writer = "+".join(sorted(models)) if models else "no_v2_prose_provenance"
        writer_tier[(writer, tier)] += 1
        writer_domain[(writer, lexeme_domain.get(lid, "none"))] += 1
        writer_source[(writer, str(source))] += 1
    writer_totals: Counter[str] = Counter()
    for (w, _), c in writer_tier.items():
        writer_totals[w] += c
    source_table: dict[str, dict[str, int]] = defaultdict(dict)
    for (w, s), c in writer_source.items():
        source_table[w][s] = c
    rows = stage_model.sort_by([("stage", "ascending"), ("count_all", "descending")]).to_pylist()
    prose_rows = [r for r in rows if r["stage"] in PROSE_STAGES]
    prose_total = sum(r["output_tokens_sum"] or 0 for r in prose_rows)
    by_model: Counter[str] = Counter()
    for r in prose_rows:
        by_model[r["model"]] += r["output_tokens_sum"] or 0
    return {
        "stage_model": [
            {
                "stage": r["stage"],
                "model": r["model"],
                "records": r["count_all"],
                "cost_usd": round(r["cost_usd_sum"] or 0.0, 2),
                "output_tokens": r["output_tokens_sum"],
            }
            for r in rows
        ],
        "prose_output_token_share_by_model": {
            m: round(c / prose_total, 6) for m, c in by_model.most_common()
        },
        "live_lexemes_by_prose_writer_set": dict(writer_totals.most_common()),
        "live_lexemes_by_prose_writer_set_and_source": {
            k: dict(sorted(v.items())) for k, v in source_table.items()
        },
        "writer_vs_tier_mi": mutual_information(writer_tier),
        "writer_vs_domain_mi": mutual_information(writer_domain),
        "note": (
            "no_v2_prose_provenance marks live lexemes whose prose predates v2 generation (text "
            "migrated from v1.x or imported from WordNet); their writer is not in v2.3 provenance"
        ),
    }


# --------------------------------------------------------------------------------------


def main() -> None:
    """Run the census and write the JSON."""
    global _SENSE_DOMAIN, _LEXEME_DOMAIN, _TOKENIZER_PATH, RELEASE, HF_ROOT  # noqa: PLW0603
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("reports/census-v2.3/census.json"))
    parser.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER)
    parser.add_argument("--verify-hub", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", nargs="*", help="family names to run (default all)")
    parser.add_argument("--release", default=RELEASE, help="release label in repo names")
    parser.add_argument("--hf-root", type=Path, default=HF_ROOT, help="local export root")
    args = parser.parse_args()
    # Mirrored into the environment so the pool workers see the same values.
    RELEASE, HF_ROOT = args.release, args.hf_root
    os.environ["OPENGLOSS_CENSUS_RELEASE"] = RELEASE
    os.environ["OPENGLOSS_CENSUS_HF_ROOT"] = str(HF_ROOT)

    started = time.time()
    _TOKENIZER_PATH = str(args.tokenizer.resolve())
    _init_hashes()
    _log("[census] coverage")
    cov, _SENSE_DOMAIN, _LEXEME_DOMAIN = coverage()

    fams = [f for f in families() if not args.only or f.name in args.only]
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(_SENSE_DOMAIN, _LEXEME_DOMAIN, _TOKENIZER_PATH),
    ) as pool:
        # Longest first so the pretrain family does not start last.
        order = sorted(fams, key=lambda f: f.name != "pretrain")
        futures = {f.name: pool.submit(run_family, f) for f in order}
        _log("[census] inventory")
        inv = inventory(args.verify_hub)
        _log("[census] components")
        comp = components(_SENSE_DOMAIN)
        _log("[census] supervision")
        sup = supervision()
        _log("[census] reuse/exposure")
        reuse = reuse_and_exposure()
        _log("[census] pretrain verbatim reuse")
        verbatim = pretrain_verbatim_reuse()
        _log("[census] writers")
        wr = writers(_LEXEME_DOMAIN)
        fam_results = {f.name: futures[f.name].result() for f in fams}

    census = {
        "census": "opengloss-g0-v2.3",
        "plan": "docs/ENCODER-DATA-EXPANSION-PLAN.md#phase-g0--census-and-design-freeze",
        "release": RELEASE,
        "model_calls": 0,
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "seconds": round(time.time() - started, 1),
            "tokenizer_file": args.tokenizer.name,
            "tokenizer_sha256": sha256_file(args.tokenizer),
            "tokenizers_version": tokenizers.__version__,
            "pyarrow_version": pa.__version__,
            "numpy_version": np.__version__,
            "contexts": list(CONTEXTS),
            "sample_words": SAMPLE_WORDS,
            "sample_openers": SAMPLE_OPENERS,
            "stratum_sample_words": STRATUM_SAMPLE_WORDS,
            "stratum_sample_openers": STRATUM_SAMPLE_OPENERS,
            "word_regex": _WORD_RE.pattern,
            "normalization": "casefold, collapse whitespace, strip",
        },
        "inventory": inv,
        "coverage": cov,
        "components": comp,
        "families": fam_results,
        "family_totals": {
            "rows": sum(r["rows"] for r in fam_results.values()),
            "tokens": sum(r["tokens"] for r in fam_results.values()),
            "unique_text_tokens": sum(r["unique_text_tokens"] for r in fam_results.values()),
            "source_tokens_excluding_pretrain": sum(
                r["unique_text_tokens"] for k, r in fam_results.items() if k != "pretrain"
            ),
        },
        "supervision": sup,
        "effective_information": {**reuse, "pretrain_verbatim_reuse": verbatim},
        "writers": wr,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(census, indent=1, ensure_ascii=False, sort_keys=False) + "\n")
    _log(f"[census] wrote {args.out} in {census['meta']['seconds']}s")


if __name__ == "__main__":
    main()
