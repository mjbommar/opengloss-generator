"""Writer-attribution classifier for the writer-diversity pilot (D-63).

TF-IDF + balanced multinomial logistic regression, 5-fold cross-validated, following
SHELF's method (shelf-paper/latex/sections/03_corpus.tex): report accuracy against
chance, a full confusion matrix, per-pair (one-vs-one) accuracy, and the top
discriminating word features per writer. Lower overall accuracy means less fingerprint —
the property a writer-diversity mix is supposed to buy.

Run with the extra dependency this one script needs, not the project's own environment:

    uv run --with scikit-learn python scripts/attribution_classifier.py \
        --input data/writer-texts-luna.jsonl data/writer-texts-qwen.jsonl ... \
        --out docs/writer-diversity-attribution.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict

_SEED = 42
_TOP_FEATURES = 15


def _load_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _balance(rows: list[dict]) -> list[dict]:
    """Undersample every writer to the smallest writer's row count."""
    by_writer: dict[str, list[dict]] = {}
    for row in rows:
        by_writer.setdefault(row["writer"], []).append(row)
    n = min(len(v) for v in by_writer.values())
    rng = random.Random(_SEED)  # noqa: S311 - balancing a sample, not crypto
    balanced = []
    for writer, writer_rows in by_writer.items():
        balanced.extend(rng.sample(writer_rows, n))
    return balanced


def main() -> None:
    """Run the classifier and print/save a full report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    rows = _balance(_load_rows(args.input))
    texts = [r["text"] for r in rows]
    writers = sorted({r["writer"] for r in rows})
    y = np.array([writers.index(r["writer"]) for r in rows])
    counts = Counter(r["writer"] for r in rows)

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=20_000)
    x = vectorizer.fit_transform(texts)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=_SEED)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=_SEED)
    y_pred = cross_val_predict(clf, x, y, cv=cv)
    accuracy = float((y_pred == y).mean())
    chance = 1.0 / len(writers)
    cm = confusion_matrix(y, y_pred).tolist()

    # Per-pair (one-vs-one) accuracy: how attributable is writer A vs writer B alone.
    pairwise: dict[str, float] = {}
    for i, a in enumerate(writers):
        for b in writers[i + 1 :]:
            mask = (y == writers.index(a)) | (y == writers.index(b))
            sub_x, sub_y = x[mask], y[mask]
            sub_pred = cross_val_predict(clf, sub_x, sub_y, cv=cv)
            pairwise[f"{a} vs {b}"] = float((sub_pred == sub_y).mean())

    # Fit once on everything for the human-readable top features per writer.
    clf.fit(x, y)
    feature_names = vectorizer.get_feature_names_out()
    top_features: dict[str, list[str]] = {}
    coefs = clf.coef_ if len(writers) > 2 else np.vstack([-clf.coef_[0], clf.coef_[0]])
    for i, writer in enumerate(writers):
        top_idx = np.argsort(coefs[i])[::-1][:_TOP_FEATURES]
        top_features[writer] = [feature_names[j] for j in top_idx]

    report = {
        "n_rows_per_writer": dict(counts),
        "n_rows_balanced_per_writer": len(rows) // len(writers),
        "writers": writers,
        "accuracy": accuracy,
        "chance": chance,
        "confusion_matrix": cm,
        "confusion_matrix_labels": writers,
        "pairwise_accuracy": pairwise,
        "top_features": top_features,
    }
    print(json.dumps(report, indent=2))
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
