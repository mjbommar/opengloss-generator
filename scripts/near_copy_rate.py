"""Measure the near-copy rate and diversity distribution of register renditions.

F7 / D-59. Zero model calls: this only reads a store and computes
:func:`~opengloss_generator.hygiene.lexical_diversity` / ``is_near_copy`` over text
already on disk.

Run once against ``data/sample-300`` before F7 shipped, to measure the baseline the
feature is meant to fix, and again afterwards to see the effect of the prompt change
(the 300-entry store predates the prompt-version bump, so a second run shows the same
baseline until the store is regenerated or enriched under the new instructions).

Usage:
    uv run python scripts/near_copy_rate.py --store data/sample-300
    uv run python scripts/near_copy_rate.py --store data/sample-300 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.hygiene import NEAR_COPY_JACCARD_THRESHOLD, lexical_diversity
from opengloss_generator.schema import Register
from opengloss_generator.store import LexemeStore

#: Histogram bucket edges for the diversity distribution, matching the 0.30-0.60 target
#: band the rendition instructions ask for so the report reads directly against it.
_BUCKETS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

#: The target lexical-diversity band :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS`
#: asks a register rewrite to land in.
_TARGET_BAND = (0.30, 0.60)


def _bucket_label(index: int) -> str:
    """Return the ``"[lo, hi)"`` label for one histogram bucket, closed at 1.0."""
    lo, hi = _BUCKETS[index], _BUCKETS[index + 1]
    closer = "]" if hi == 1.0 else ")"
    return f"[{lo:.1f}, {hi:.1f}{closer}"


def _bucket_index(value: float) -> int:
    """Return which bucket a diversity value falls into."""
    for index in range(len(_BUCKETS) - 1):
        lo, hi = _BUCKETS[index], _BUCKETS[index + 1]
        if lo <= value < hi or (hi == 1.0 and value == 1.0):
            return index
    return len(_BUCKETS) - 2


def measure(store: LexemeStore) -> dict[str, object]:
    """Return the near-copy rate and diversity distribution over one store.

    Only non-``plain``-register gloss renditions are measured against their sense's
    canonical gloss -- the population F7's generation-time and retrofit checks both
    apply to (D-59).

    Args:
        store: The store to read. Never mutated.

    Returns:
        A JSON-serialisable report: entry/sense/rendition counts, the near-copy rate,
        summary statistics, and a histogram of the diversity distribution.
    """
    diversities: list[float] = []
    entries_scanned = 0
    senses_with_canonical = 0

    for entry in store.iter_entries():
        entries_scanned += 1
        for _, sense, _ in entry.iter_senses():
            if sense.retired:
                continue
            canonical = sense.canonical_gloss()
            if not canonical:
                continue
            senses_with_canonical += 1
            for rendition in sense.gloss:
                if rendition.style is Register.PLAIN:
                    continue
                diversities.append(lexical_diversity(rendition.content, canonical))

    near_copy_count = sum(
        1 for diversity in diversities if 1.0 - diversity >= NEAR_COPY_JACCARD_THRESHOLD
    )
    histogram = [0] * (len(_BUCKETS) - 1)
    for diversity in diversities:
        histogram[_bucket_index(diversity)] += 1
    band_lo, band_hi = _TARGET_BAND
    in_band = sum(1 for d in diversities if band_lo <= d <= band_hi)

    n = len(diversities)
    return {
        "store": str(store.root),
        "entries_scanned": entries_scanned,
        "senses_with_canonical_gloss": senses_with_canonical,
        "register_renditions_measured": n,
        "near_copy_threshold_jaccard": NEAR_COPY_JACCARD_THRESHOLD,
        "near_copy_count": near_copy_count,
        "near_copy_rate": (near_copy_count / n) if n else None,
        "diversity_mean": statistics.fmean(diversities) if n else None,
        "diversity_median": statistics.median(diversities) if n else None,
        "diversity_min": min(diversities) if n else None,
        "diversity_max": max(diversities) if n else None,
        "diversity_stdev": statistics.pstdev(diversities) if n > 1 else (0.0 if n else None),
        "diversity_histogram": {_bucket_label(i): count for i, count in enumerate(histogram)},
        "target_band": list(_TARGET_BAND),
        "share_in_target_band": (in_band / n) if n else None,
    }


def render_text(report: dict[str, object]) -> str:
    """Return the report as human-readable text."""
    lines = [
        f"store: {report['store']}",
        f"entries scanned: {report['entries_scanned']}",
        f"senses with a canonical gloss: {report['senses_with_canonical_gloss']}",
        f"non-plain gloss renditions measured: {report['register_renditions_measured']}",
    ]
    if not report["register_renditions_measured"]:
        lines.append("no non-plain gloss renditions found.")
        return "\n".join(lines)

    band_lo, band_hi = report["target_band"]
    lines.extend(
        [
            f"near-copy rate (Jaccard >= {report['near_copy_threshold_jaccard']}): "
            f"{report['near_copy_count']}/{report['register_renditions_measured']} "
            f"= {report['near_copy_rate']:.1%}",
            f"lexical diversity: mean={report['diversity_mean']:.3f} "
            f"median={report['diversity_median']:.3f} "
            f"min={report['diversity_min']:.3f} max={report['diversity_max']:.3f} "
            f"stdev={report['diversity_stdev']:.3f}",
            f"share inside the {band_lo:.2f}-{band_hi:.2f} target band: "
            f"{report['share_in_target_band']:.1%}",
            "diversity histogram:",
        ]
    )
    lines.extend(f"  {label}: {count}" for label, count in report["diversity_histogram"].items())
    return "\n".join(lines)


def main() -> None:
    """Parse arguments, run the measurement, and print the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store", type=Path, default=Path("data/sample-300"), help="Store root to measure."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON instead of text."
    )
    args = parser.parse_args()

    store = LexemeStore(StoreConfig(root=args.store, fsync_on_write=False))
    report = measure(store)
    output = json.dumps(report, indent=2) if args.json else render_text(report)
    print(output)  # noqa: T201 - this script's whole job is to report to stdout


if __name__ == "__main__":
    main()
